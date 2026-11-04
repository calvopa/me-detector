"""Clientes de las APIs públicas: GeckoTerminal (descubrimiento + velas), DexScreener (métricas
de mercado en lote), RugCheck (seguridad del token) y CoinGecko (exchanges centralizados). Sin claves."""
import time

import requests

import config

_session = requests.Session()
_session.headers["accept"] = "application/json"
_ultimo: dict[str, float] = {}

# Tokens de cotización: nunca son candidatos
QUOTE_MINTS = {
    "So11111111111111111111111111111111111111112",   # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
}

# Exchanges centralizados que se informan: id de mercado en CoinGecko → nombre
CEX_CONOCIDOS = {
    "binance": "Binance", "gdax": "Coinbase", "okex": "OKX", "bybit_spot": "Bybit", "kraken": "Kraken",
    "kucoin": "KuCoin", "gate": "Gate", "bitget": "Bitget", "mxc": "MEXC", "huobi": "HTX",
}


def _get(api: str, url: str, params: dict | None = None, intentos: int = 3):
    for i in range(intentos):
        espera = config.THROTTLE_S[api] - (time.time() - _ultimo.get(api, 0))
        if espera > 0:
            time.sleep(espera)
        _ultimo[api] = time.time()
        try:
            r = _session.get(url, params=params, timeout=20)
            if r.status_code == 429:
                time.sleep(15 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.exceptions.RequestException:
            if i == intentos - 1:
                raise
            time.sleep(2 * (i + 1))
    raise requests.exceptions.RequestException(f"{api}: rate limit persistente")


# ── GeckoTerminal ─────────────────────────────────────────────────────────────

def _mints_de_pools(data: list) -> set[str]:
    out = set()
    for p in data:
        mint = p["relationships"]["base_token"]["data"]["id"].split("_", 1)[1]
        if mint not in QUOTE_MINTS:
            out.add(mint)
    return out


def descubrir() -> set[str]:
    """Mints candidatos: pools nuevos, pools en tendencia (1h) y los de PumpSwap con más
    operaciones (ahí caen los tokens recién graduados de pump.fun)."""
    base = f"{config.GECKOTERMINAL_API}/networks/solana"
    pedidos = [(f"{base}/new_pools", {"page": p}) for p in range(1, config.GT_NEW_POOLS_PAGES + 1)]
    pedidos += [(f"{base}/trending_pools", {"duration": "1h", "page": p})
                for p in range(1, config.GT_TRENDING_PAGES + 1)]
    pedidos += [(f"{base}/dexes/pumpswap/pools", {"sort": "h24_tx_count_desc", "page": p}) for p in (1, 2)]
    mints = set()
    for url, params in pedidos:
        try:
            mints |= _mints_de_pools(_get("gt", url, params).get("data", []))
        except requests.exceptions.RequestException:
            continue  # una página caída no frena el resto
    return mints


def velas(pool: str, mint: str, desde_s: int, hasta_s: int) -> list[dict]:
    """Velas de 15 min del pool entre desde_s y hasta_s (precio del token en USD). Se pide por mint
    porque el "base" de GeckoTerminal puede ser el otro token del pool."""
    n = min(1000, int((hasta_s - desde_s) / 900) + 2)
    d = _get("gt", f"{config.GECKOTERMINAL_API}/networks/solana/pools/{pool}/ohlcv/minute",
             {"aggregate": 15, "before_timestamp": hasta_s, "limit": n, "token": mint})
    lista = d["data"]["attributes"]["ohlcv_list"]
    return [{"t": v[0], "o": v[1], "h": v[2], "l": v[3], "c": v[4]}
            for v in sorted(lista) if v[0] + 900 > desde_s]


# ── DexScreener ───────────────────────────────────────────────────────────────

def _sumar_par(out: dict[str, dict], p: dict):
    """Suma el par a las métricas de su token. Liquidez solo de pares que la informan (las curvas
    de bonding no la informan)."""
    mint = p["baseToken"]["address"]
    m = out.setdefault(mint, {"mint": mint, "symbol": p["baseToken"].get("symbol"),
                              "name": p["baseToken"].get("name"), "liq": 0.0, "vol_h1": 0.0,
                              "buys_h1": 0, "sells_h1": 0, "creado_ms": None, "par": None, "liq_dex": {}})
    h1 = (p.get("txns") or {}).get("h1") or {}
    m["vol_h1"] += float((p.get("volume") or {}).get("h1") or 0)
    m["buys_h1"] += int(h1.get("buys") or 0)
    m["sells_h1"] += int(h1.get("sells") or 0)
    if p.get("pairCreatedAt"):
        m["creado_ms"] = min(m["creado_ms"] or p["pairCreatedAt"], p["pairCreatedAt"])
    liq = float((p.get("liquidity") or {}).get("usd") or 0)
    if liq:
        dex = p.get("dexId") or "?"
        m["liq_dex"][dex] = m["liq_dex"].get(dex, 0.0) + liq
    if liq > m["liq"]:
        # el par con más liquidez manda el precio, el FDV y el pool que se sigue
        m.update(liq=liq, par=p["pairAddress"], dex=p.get("dexId"),
                 precio=float(p.get("priceUsd") or 0), fdv=p.get("fdv"))


def mercado(mints: list[str]) -> dict[str, dict]:
    """{mint: métricas} en lote. Este endpoint trae un solo par por token, el principal: alcanza
    para descartar y para el seguimiento; para decidir un aviso, ver token_completo()."""
    out: dict[str, dict] = {}
    for i in range(0, len(mints), 30):
        lote = mints[i:i + 30]
        try:
            pares = _get("ds", f"{config.DEXSCREENER_API}/tokens/v1/solana/{','.join(lote)}")
        except requests.exceptions.RequestException:
            continue
        for p in pares or []:
            if p["baseToken"]["address"] in lote:  # si no, aparece como cotización de otro par
                _sumar_par(out, p)
    return out


def token_completo(mint: str) -> dict | None:
    """Métricas sumando todos los pares del token, curva incluida. None si no tiene pares."""
    out: dict[str, dict] = {}
    for p in _get("ds", f"{config.DEXSCREENER_API}/token-pairs/v1/solana/{mint}") or []:
        if p["baseToken"]["address"] == mint:
            _sumar_par(out, p)
    return out.get(mint)


# ── CoinGecko ─────────────────────────────────────────────────────────────────

def cex(mint: str) -> list[str] | None:
    """CEX conocidos donde cotiza el token. [] si CoinGecko no lo lista (lo normal en un token
    recién graduado), None si no se pudo consultar. Un solo intento: una falla no frena el aviso."""
    try:
        d = _get("cg", f"{config.COINGECKO_API}/coins/solana/contract/{mint}", intentos=1)
    except requests.exceptions.HTTPError as e:
        return [] if e.response.status_code == 404 else None
    except requests.exceptions.RequestException:
        return None
    mercados = {(t.get("market") or {}).get("identifier") for t in d.get("tickers") or []}
    return [nombre for id_cg, nombre in CEX_CONOCIDOS.items() if id_cg in mercados]


# ── RugCheck ──────────────────────────────────────────────────────────────────

def seguridad(mint: str) -> dict:
    d = _get("rc", f"{config.RUGCHECK_API}/tokens/{mint}/report")
    conocidas = d.get("knownAccounts") or {}
    holders = [h for h in d.get("topHolders") or []
               if (conocidas.get(h["owner"]) or {}).get("type") not in ("AMM", "LOCKER")]
    return {
        "holders": d.get("totalHolders") or 0,
        "top10": round(sum(h["pct"] for h in holders[:10]), 1),
        "mint_auth": d.get("mintAuthority"),
        "freeze_auth": d.get("freezeAuthority"),
        "rugged": bool(d.get("rugged")),
        "dangers": [r["name"] for r in d.get("risks") or [] if r.get("level") == "danger"],
        "warns": [r["name"] for r in d.get("risks") or [] if r.get("level") == "warn"],
    }
