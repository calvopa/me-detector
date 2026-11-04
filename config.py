import os

from dotenv import load_dotenv

load_dotenv()

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "180"))

# Subir al cambiar filtros o medición: las estadísticas solo mezclan avisos de la misma versión.
# v1: hasta 04/11/2026 (velas invertidas en algunos pools, máximos con la vela previa al aviso).
FILTROS_V = 2

# ── Filtro de mercado (DexScreener) ──
# Edad del token = el par más viejo (la curva de pump.fun cuenta): de 30 min a 24 h.
MIN_AGE_MIN = float(os.getenv("MIN_AGE_MIN", "30"))
MAX_AGE_H = float(os.getenv("MAX_AGE_H", "24"))
# Liquidez solo de pools AMM (la curva de pump.fun no informa liquidez → un token sin graduar no pasa)
MIN_LIQ_USD = float(os.getenv("MIN_LIQ_USD", "30000"))
MIN_VOL_H1_USD = float(os.getenv("MIN_VOL_H1_USD", "50000"))
# Ventas en la última hora: si nadie vende puede ser un honeypot
MIN_SELLS_H1 = int(os.getenv("MIN_SELLS_H1", "20"))
# Muchas compras por cada venta (última hora): posible honeypot. En vivo lo normal fue 1 a 2,4.
MAX_COMPRAS_POR_VENTA = float(os.getenv("MAX_COMPRAS_POR_VENTA", "5"))

# ── Filtro de seguridad (RugCheck) ──
MIN_HOLDERS = int(os.getenv("MIN_HOLDERS", "300"))
# % de los 10 mayores holders, sin contar pools (AMM) ni lockers
MAX_TOP10_PCT = float(os.getenv("MAX_TOP10_PCT", "30"))
# Cada cuánto se vuelve a pedir el reporte de un token que todavía no pasó
RUGCHECK_TTL_MIN = float(os.getenv("RUGCHECK_TTL_MIN", "30"))

# ── Seguimiento de avisos ──
HORIZONTES_H = [1, 6, 24, 168]   # 1h, 6h, 24h, 7d
# Rug: la liquidez cae por debajo de esta fracción de la que había al avisar
RUG_LIQ_FRAC = float(os.getenv("RUG_LIQ_FRAC", "0.2"))

# ── APIs públicas, sin clave ──
GECKOTERMINAL_API = "https://api.geckoterminal.com/api/v2"
DEXSCREENER_API = "https://api.dexscreener.com"
RUGCHECK_API = "https://api.rugcheck.xyz/v1"
COINGECKO_API = "https://api.coingecko.com/api/v3"
# Espera mínima entre llamadas a cada API (GeckoTerminal free: ~30/min; CoinGecko sin clave: ~10/min)
THROTTLE_S = {"gt": 2.2, "ds": 0.3, "rc": 1.5, "cg": 6.0}
GT_NEW_POOLS_PAGES = int(os.getenv("GT_NEW_POOLS_PAGES", "3"))
GT_TRENDING_PAGES = int(os.getenv("GT_TRENDING_PAGES", "2"))

# Telegram — solo envío (sin polling), reusa el bot de cryptobot sin conflicto
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
RESUMEN_HOUR = int(os.getenv("RESUMEN_HOUR", "9"))  # resumen diario, hora ART
