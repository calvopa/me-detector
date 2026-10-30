"""Detector de memecoins de Solana. NO opera: no tiene wallet ni claves. Busca tokens recién
graduados con tracción, descarta los inseguros, avisa por Telegram y mide qué pasó con cada
aviso a 1h / 6h / 24h / 7d."""
import csv
import html
import io
import json
import logging
import os
import time
from collections import Counter
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

import requests

import config
import db
import engine
import fuentes

_RUNTIME = Path(os.getenv("RUNTIME_DIR", "."))
_RUNTIME.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("urllib3").setLevel(logging.WARNING)
log = logging.getLogger("detector")
_fh = RotatingFileHandler(str(_RUNTIME / "detector.log"), maxBytes=5_000_000, backupCount=3)
_fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
logging.getLogger().addHandler(_fh)

DB_FILE = _RUNTIME / "detector.db"
STATUS_FILE = _RUNTIME / "status.json"
CSV_FILE = _RUNTIME / "alerts.csv"
HEARTBEAT_FILE = _RUNTIME / "heartbeat"
ART = timezone(timedelta(hours=-3))

CSV_COLS = ["ts", "symbol", "name", "mint", "pool", "dex", "precio", "liq", "vol_h1", "fdv", "holders",
            "top10", "edad_min", "r1h", "r6h", "r24h", "r7d", "max_pct", "min_pct", "rug_ts"]


def _escribir_atomico(path: Path, texto: str):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(texto)
    tmp.rename(path)


# ── Telegram (solo envío) ─────────────────────────────────────────────────────

def telegram(texto: str):
    if not config.TELEGRAM_TOKEN or not config.TELEGRAM_CHAT_ID:
        log.info("Telegram no configurado — mensaje no enviado")
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{config.TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": config.TELEGRAM_CHAT_ID, "text": texto, "parse_mode": "HTML",
                  "disable_web_page_preview": "true"},
            timeout=15,
        )
        if not r.ok:
            log.warning("Telegram respondió %s", r.status_code)
    except requests.exceptions.RequestException as e:
        # nunca loguear la excepción completa: incluye la URL con el token
        log.warning("Error enviando a Telegram: %s", type(e).__name__)


def _usd(x):
    if x is None:
        return "—"
    return f"${x / 1e6:.2f}M" if x >= 1e6 else f"${x / 1e3:.0f}k"


def _pct(x):
    return "—" if x is None else f"{x:+.0f}%"


def texto_aviso(m: dict, s: dict, edad_min: float) -> str:
    sym = html.escape(m.get("symbol") or "?")
    nombre = html.escape(m.get("name") or "")
    avisos = f"\n⚠️ {html.escape(', '.join(s['warns']))}" if s["warns"] else ""
    return (
        f"🔎 <b>Memecoin: ${sym}</b> — {nombre}\n"
        f"Edad {edad_min / 60:.1f}h · {m.get('dex')}\n"
        f"Liquidez {_usd(m['liq'])} · Vol 1h {_usd(m['vol_h1'])} · FDV {_usd(m.get('fdv'))}\n"
        f"Compras/ventas 1h: {m['buys_h1']}/{m['sells_h1']}\n"
        f"Holders {s['holders']} · Top10 {s['top10']:.0f}%{avisos}\n"
        f"<code>{m['mint']}</code>\n"
        f"<a href=\"https://dexscreener.com/solana/{m['par']}\">DexScreener</a> · "
        f"<a href=\"https://rugcheck.xyz/tokens/{m['mint']}\">RugCheck</a>\n"
        f"<i>Solo detección — no es recomendación de compra</i>"
    )


def texto_resumen(con) -> str:
    st = engine.estadisticas(con)
    desde = int((time.time() - 86_400) * 1000)
    ayer = con.execute("SELECT symbol, r1h, max_pct, rug_ts FROM alerts WHERE ts >= ? ORDER BY ts",
                       (desde,)).fetchall()
    lineas = [f"📊 <b>Detector de memecoins — resumen {datetime.now(ART):%d/%m}</b>",
              f"Avisos últimas 24h: {len(ayer)} · total: {st['avisos']}"]
    for a in ayer[:15]:
        rug = " 🪤rug" if a["rug_ts"] else ""
        lineas.append(f"• ${html.escape(a['symbol'] or '?')}: 1h {_pct(a['r1h'])}, máx {_pct(a['max_pct'])}{rug}")
    r24 = st["r24h"]
    if r24["n"]:
        lineas.append(f"\nComprando al aviso y vendiendo a las 24h ({r24['n']} avisos): mediana "
                      f"{_pct(r24['mediana'])}, promedio {_pct(r24['promedio'])}, "
                      f"{r24['positivos_pct']}% en positivo")
        lineas.append(f"Llegaron a x2: {st['x2_pct']}% · rug: {st['rug_pct_24h']}%")
    lineas.append("<i>Sin comisiones ni slippage: el resultado real sería peor</i>")
    return "\n".join(lineas)


# ── Ciclo ─────────────────────────────────────────────────────────────────────

def evaluar(con, ahora_ms: int) -> dict:
    nuevos = fuentes.descubrir()
    con.executemany("INSERT OR IGNORE INTO watch(mint, first_seen) VALUES(?, ?)",
                    [(m, ahora_ms) for m in nuevos])
    # fuera de la lista lo que ya no puede pasar el filtro de edad (+1h de margen)
    con.execute("DELETE FROM watch WHERE first_seen < ?", (ahora_ms - (config.MAX_AGE_H + 1) * 3_600_000,))
    pendientes = [r["mint"] for r in con.execute(
        "SELECT mint FROM watch WHERE mint NOT IN (SELECT mint FROM alerts)")]
    mercado = fuentes.mercado(pendientes)
    embudo = Counter(descubiertos=len(nuevos), en_lista=len(pendientes))

    for mint in pendientes:
        m = mercado.get(mint)
        motivo = engine.filtro_mercado(m, ahora_ms) if m else "sin pares"
        if motivo is None:
            embudo["pasan_mercado"] += 1
            w = con.execute("SELECT rc_ts, motivo FROM watch WHERE mint=?", (mint,)).fetchone()
            if w["rc_ts"] and ahora_ms - w["rc_ts"] < config.RUGCHECK_TTL_MIN * 60_000:
                motivo = w["motivo"]  # reporte reciente: no volver a pedirlo
            else:
                try:
                    s = fuentes.seguridad(mint)
                except Exception:
                    embudo["error rugcheck"] += 1
                    continue
                con.execute("UPDATE watch SET rc_ts=? WHERE mint=?", (ahora_ms, mint))
                motivo = engine.filtro_seguridad(s)
                if motivo is None:
                    avisar(con, m, s, ahora_ms)
                    embudo["avisos"] += 1
        if motivo:
            embudo[motivo] += 1
        con.execute("UPDATE watch SET motivo=? WHERE mint=?", (motivo, mint))
    return dict(embudo)


def avisar(con, m: dict, s: dict, ahora_ms: int):
    edad_min = (ahora_ms - m["creado_ms"]) / 60_000
    con.execute(
        "INSERT OR IGNORE INTO alerts(mint, symbol, name, ts, pool, dex, precio, liq, vol_h1, fdv, holders,"
        " top10, edad_min, precio_max, precio_min, ultimo, ultima_liq)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (m["mint"], m.get("symbol"), m.get("name"), ahora_ms, m["par"], m.get("dex"), m["precio"], m["liq"],
         m["vol_h1"], m.get("fdv"), s["holders"], s["top10"], round(edad_min), m["precio"], m["precio"],
         m["precio"], m["liq"]))
    log.info("Aviso: %s %s liq=%.0f vol1h=%.0f holders=%s top10=%s", m.get("symbol"), m["mint"],
             m["liq"], m["vol_h1"], s["holders"], s["top10"])
    telegram(texto_aviso(m, s, edad_min))


def escribir_salidas(con, embudo: dict):
    filas = con.execute("SELECT * FROM alerts ORDER BY ts").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for a in filas:
        w.writerow([datetime.fromtimestamp(a["ts"] / 1000, ART).isoformat() if c == "ts" else a[c]
                    for c in CSV_COLS])
    _escribir_atomico(CSV_FILE, buf.getvalue())
    recientes = [dict(a) for a in filas[-30:]][::-1]
    _escribir_atomico(STATUS_FILE, json.dumps({
        "actualizado": datetime.now(ART).isoformat(timespec="seconds"),
        "embudo": embudo,
        "estadisticas": engine.estadisticas(con),
        "recientes": recientes,
        "filtros": {k: getattr(config, k) for k in (
            "MIN_AGE_MIN", "MAX_AGE_H", "MIN_LIQ_USD", "MIN_VOL_H1_USD", "MIN_SELLS_H1",
            "MIN_HOLDERS", "MAX_TOP10_PCT")},
    }, indent=2, ensure_ascii=False, default=str))


def toca_resumen(con) -> bool:
    ahora = datetime.now(ART)
    return ahora.hour >= config.RESUMEN_HOUR and db.get_state(con, "ultimo_resumen") != ahora.date().isoformat()


def ciclo(con):
    ahora_ms = int(time.time() * 1000)
    embudo = evaluar(con, ahora_ms)
    engine.actualizar_snapshots(con, ahora_ms)
    engine.cerrar_horizontes(con, ahora_ms)
    con.commit()
    escribir_salidas(con, embudo)
    log.info("Ciclo: %s", embudo)
    if toca_resumen(con):
        telegram(texto_resumen(con))
        db.set_state(con, "ultimo_resumen", datetime.now(ART).date().isoformat())
        con.commit()


def main():
    con = db.connect(DB_FILE)
    log.info("Detector de memecoins arrancado — sin wallet, solo lectura")
    while True:
        t0 = time.time()
        try:
            ciclo(con)
            HEARTBEAT_FILE.write_text(datetime.now(ART).isoformat())
        except Exception:
            log.exception("Error en el ciclo")
        time.sleep(max(10, config.POLL_INTERVAL_SECONDS - (time.time() - t0)))


if __name__ == "__main__":
    main()
