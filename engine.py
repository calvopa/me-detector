"""Filtros y seguimiento. El detector nunca compra: cada aviso se mide como si se hubiera
comprado al precio del aviso, sin comisiones ni slippage (en pools chicos son altos: el
resultado real sería peor)."""
import statistics
import time

import config
import fuentes

HORIZONTE_COL = {1: "r1h", 6: "r6h", 24: "r24h", 168: "r7d"}


def filtro_mercado(m: dict, ahora_ms: int) -> str | None:
    """None si pasa; si no, el motivo (para calibrar los umbrales después)."""
    if not m.get("creado_ms"):
        return "sin fecha"
    edad_min = (ahora_ms - m["creado_ms"]) / 60_000
    if edad_min < config.MIN_AGE_MIN:
        return "muy nuevo"
    if edad_min > config.MAX_AGE_H * 60:
        return "muy viejo"
    if m["liq"] < config.MIN_LIQ_USD:
        return "poca liquidez"
    if m["vol_h1"] < config.MIN_VOL_H1_USD:
        return "poco volumen"
    if m["sells_h1"] < config.MIN_SELLS_H1:
        return "pocas ventas"
    return None


def filtro_seguridad(s: dict) -> str | None:
    if s["rugged"]:
        return "rugged"
    if s["mint_auth"] or s["freeze_auth"]:
        return "mint/freeze authority"
    if s["dangers"]:
        return "rugcheck danger"
    if s["holders"] < config.MIN_HOLDERS:
        return "pocos holders"
    if s["top10"] > config.MAX_TOP10_PCT:
        return "top10 concentrado"
    return None


def _pct(precio, base):
    return round((precio / base - 1) * 100, 1) if precio and base else None


def actualizar_snapshots(con, ahora_ms: int):
    """Precio y liquidez actuales de los avisos de la última semana (+1h de margen)."""
    vivos = con.execute("SELECT * FROM alerts WHERE ts > ?", (ahora_ms - 169 * 3_600_000,)).fetchall()
    if not vivos:
        return
    mercado = fuentes.mercado([a["mint"] for a in vivos])
    for a in vivos:
        m = mercado.get(a["mint"])
        liq = m["liq"] if m else 0.0
        precio = m["precio"] if m and m.get("precio") else a["ultimo"]
        rug_ts = a["rug_ts"]
        if not rug_ts and a["liq"] and liq < a["liq"] * config.RUG_LIQ_FRAC:
            rug_ts = ahora_ms
        con.execute(
            "UPDATE alerts SET ultimo=?, ultima_liq=?, rug_ts=?, precio_max=MAX(precio_max, ?),"
            " precio_min=MIN(precio_min, ?) WHERE id=?",
            (precio, liq, rug_ts, precio or a["precio_max"], precio or a["precio_min"], a["id"]))


def cerrar_horizontes(con, ahora_ms: int):
    """Para cada horizonte cumplido: velas de 15 min del pool → cierre, máximo y mínimo exactos."""
    for h, col in HORIZONTE_COL.items():
        pendientes = con.execute(
            f"SELECT * FROM alerts WHERE {col} IS NULL AND ts + ? <= ?", (h * 3_600_000, ahora_ms)
        ).fetchall()
        for a in pendientes:
            desde, hasta = a["ts"] // 1000, a["ts"] // 1000 + h * 3600
            try:
                vs = fuentes.velas(a["pool"], desde, hasta)
            except Exception:
                vs = []
            if vs:
                cierre = vs[-1]["c"]
                pmax = max([v["h"] for v in vs] + [a["precio_max"] or 0])
                pmin = min([v["l"] for v in vs] + [a["precio_min"] or cierre])
            elif ahora_ms > a["ts"] + (h + 24) * 3_600_000:
                # el pool ya no tiene velas (liquidez retirada): se cierra con el último precio visto
                cierre, pmax, pmin = a["ultimo"], a["precio_max"], a["precio_min"]
            else:
                continue  # se reintenta el próximo ciclo
            con.execute(
                f"UPDATE alerts SET {col}=?, precio_max=?, precio_min=?, max_pct=?, min_pct=? WHERE id=?",
                (_pct(cierre, a["precio"]), pmax, pmin, _pct(pmax, a["precio"]), _pct(pmin, a["precio"]), a["id"]))


def estadisticas(con) -> dict:
    """Resumen para Telegram y el dashboard. Rug % sobre avisos con más de 24h."""
    ahora_ms = int(time.time() * 1000)
    out = {"avisos": con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]}
    for h, col in HORIZONTE_COL.items():
        rs = [r[0] for r in con.execute(f"SELECT {col} FROM alerts WHERE {col} IS NOT NULL")]
        out[col] = {
            "n": len(rs),
            "mediana": round(statistics.median(rs), 1) if rs else None,
            "promedio": round(statistics.fmean(rs), 1) if rs else None,
            "positivos_pct": round(100 * sum(r > 0 for r in rs) / len(rs)) if rs else None,
        }
    viejos = con.execute("SELECT rug_ts, max_pct FROM alerts WHERE ts < ?",
                         (ahora_ms - 24 * 3_600_000,)).fetchall()
    out["rug_pct_24h"] = round(100 * sum(bool(v["rug_ts"]) for v in viejos) / len(viejos)) if viejos else None
    out["x2_pct"] = round(100 * sum((v["max_pct"] or 0) >= 100 for v in viejos) / len(viejos)) if viejos else None
    return out
