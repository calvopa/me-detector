# CLAUDE.md

Guía para Claude Code en este repo.

## Qué es esto

**Detector de memecoins de Solana**: un bot que **no opera** (sin wallet, sin claves). Cada 3 min
busca tokens recién graduados de pump.fun (o lanzados en otros launchpads) con tracción, descarta
los inseguros, avisa por Telegram y **mide qué pasó con cada aviso** a 1h / 6h / 24h / 7d, como si
se hubiera comprado al precio del aviso.

Objetivo: juntar semanas de datos reales para saber si estos filtros tienen ventaja antes de pensar
en ejecutar. Nació el 30/10/2026 (ver nota `Personal/CryptoBot/Detector de memecoins` en Obsidian).

## Flujo de un ciclo

```
descubrir()  GeckoTerminal: new_pools (3 págs) + trending_pools 1h (2 págs) + PumpSwap por tx 24h (2 págs)
    └── mints nuevos → tabla watch (se borran a las MAX_AGE_H + 1h)
mercado()    DexScreener /tokens/v1/solana (lotes de 30): el par principal de cada token
    └── prefiltro_mercado: no más de 24h, liq ≥ $30k
token_completo()  DexScreener /token-pairs/v1/solana: todos los pares, solo de los que sobreviven
    └── filtro_mercado: edad 30min–24h, liq ≥ $30k, vol 1h ≥ $50k, ≥ 20 ventas 1h, ≤ 5 compras por venta
seguridad()  RugCheck /tokens/{mint}/report (cacheado RUGCHECK_TTL_MIN)
    └── filtro_seguridad: sin mint/freeze authority, sin riesgos "danger", ≥ 300 holders,
        top10 ≤ 30% (sin contar cuentas AMM/LOCKER de knownAccounts), no rugged
avisar()     alerts + Telegram (una vez por token), con DEX por liquidez, CEX según CoinGecko y link a Jupiter
seguimiento  snapshots DexScreener cada ciclo (precio, liquidez, rug) + al cumplirse cada horizonte,
             velas de 15 min de GeckoTerminal → cierre, máximo y mínimo exactos
```

Resumen diario por Telegram a las `RESUMEN_HOUR` (ART): avisos de las últimas 24h y, cuando hay datos,
mediana/promedio comprando al aviso y vendiendo a las 24h, % que llegó a x2 y % de rugs.

**Los resultados no descuentan comisiones, slippage ni priority fees** — en pools de $30-100k de
liquidez el costo real de entrar y salir es varios %. Leerlos como cota superior.

## Por qué así (lo que se probó el 30/10/2026)

- **Fuente de candidatos = pools, no lanzamientos.** pump.fun lanza miles de tokens por día (en una
  página de `new_pools` de GeckoTerminal, 20 pools son ~1 minuto). Mirar todos es inviable con 30
  llamadas/min y casi todos mueren en la curva. Se miran los pools nuevos/en tendencia y los de
  PumpSwap (donde migran los graduados).
- **Liquidez solo de pares AMM.** DexScreener devuelve `liquidity: null` en la curva de pump.fun
  (`dexId: pumpfun`) y de Meteora DBC → un token sin graduar nunca llega a $30k y no pasa.
- **Top10 sin pools.** El reporte de RugCheck cuenta la cuenta de la curva / el pool como holder
  (top10 = 100% en tokens nuevos). Se excluyen las cuentas que `knownAccounts` marca como `AMM` o
  `LOCKER`.
- **"danger" de RugCheck descarta.** En la muestra del 30/10, los graduados legítimos (HYPERCHAN,
  HEXN, HI) no tenían ningún riesgo; los que tenían eran wash trading (ZERODAY/JAPRO/PREDICT: ~50
  holders, 93% en el top10, liquidez $0, varios pools clonados del mismo nombre).
- **El "PumpSwap por volumen 24h" de GeckoTerminal está lleno de wash trading** → se ordena por
  cantidad de transacciones (`h24_tx_count_desc`), y los filtros de holders/top10 limpian el resto.
- **Edad = el par más viejo** (incluye la curva). Un token que estuvo 30h en la curva y graduó hace 2h
  queda afuera por "muy viejo".
- **Primer ciclo**: avisa de golpe los tokens de < 24h que ya cumplían (en la prueba local, 4).
  No se hizo baseline porque esos avisos también sirven como datos.

## Versiones de filtros (`FILTROS_V`)

Cada aviso guarda la versión con la que salió (`filtros_v`), y `estadisticas()` no mezcla versiones:
Telegram y el dashboard muestran la actual, `status.json` también trae la anterior en
`estadisticas_anteriores`. **Subir `FILTROS_V` al cambiar filtros o cómo se mide.** Cambiar un umbral
en `.env` no la sube sola.

- **v1 (hasta 04/11/2026)**: mediana a 24h -99%, 83% de rugs. Sus números tienen dos errores que no
  se reescribieron: velas de otro token en algunos pools de Raydium (XDG: +206M%, porque el "base" de
  GeckoTerminal era wXMR) y máximos tomados de la vela del aviso, anterior al aviso (NODF: +10.948%).
  La edad y el volumen salían de un solo par porque `tokens/v1` no trae más.
- **v2 (desde 04/11/2026)**: velas por mint, extremos solo de velas enteras tras el aviso, métricas
  con todos los pares y tope de compras por venta (`MAX_COMPRAS_POR_VENTA`, sin backtest: el historial
  no guardaba compras/ventas; desde v2 sí, para calibrarlo).

## APIs (todas públicas, sin clave — probadas en vivo el 30/10/2026)

| API | Uso | Límite / gotcha |
|---|---|---|
| GeckoTerminal `api.geckoterminal.com/api/v2` | descubrimiento + velas OHLCV | ~30/min (429 al pasarse; throttle 2.2s). `new_pools` hasta página 10. Velas: `/pools/{pool}/ohlcv/minute?aggregate=15&before_timestamp=…&token={mint}`, más nueva primero; sin `token` devuelve el "base" de GeckoTerminal, que puede ser el otro token |
| DexScreener `api.dexscreener.com/tokens/v1/solana/{mints}` | métricas, hasta 30 mints por llamada | 300/min. Un mint puede aparecer como *quote* de otro par: filtrar por `baseToken.address`. Devuelve **un solo par por token** (visto el 04/11/2026) |
| DexScreener `api.dexscreener.com/token-pairs/v1/solana/{mint}` | todos los pools del token: edad, volumen y compras/ventas para decidir el aviso, y línea DEX | hasta 30 pools; incluye la curva (`pumpfun`, sin liquidez). ~65 llamadas por ciclo |
| RugCheck `api.rugcheck.xyz/v1/tokens/{mint}/report` | seguridad | sin límite documentado; throttle 1.5s y caché de 30 min por token |
| CoinGecko `api.coingecko.com/api/v3/coins/solana/contract/{mint}` | CEX del aviso (solo los de `CEX_CONOCIDOS`) | sin clave ~10/min (429 → "CEX: sin dato"; throttle 6s, un intento). 404 = no listado → "CEX: ninguno" |

## Infraestructura

| Elemento | Valor |
|---|---|
| Código servidor | `/opt/stacks/meme-detector/` en `192.168.232.60` (srv-hometainer) |
| Código local | `/home/calvop/meme-detector/` |
| Contenedor | `meme-detector` |
| Volumen | `memedetector_data` → `/data` (`detector.db` WAL, `alerts.csv`, `status.json`, `heartbeat`, `detector.log`) |
| GitHub | `github.com/calvopa/me-detector` (privado) |

Carga: la lista `watch` ronda 16.000 tokens → ~550 lotes a DexScreener y ciclos reales de ~5 min, no 3 (medido 04/11/2026).

## Telegram

Solo **envía** (no hace `getUpdates`), así reusa `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID` de cryptobot sin
el conflicto 409. Por eso no tiene comandos. Nunca loguear excepciones de `requests` hacia Telegram
completas: la URL lleva el token.

## Comandos

```bash
# Logs (cada ciclo loguea el embudo: descubiertos → motivos de descarte → avisos)
ssh pablo@192.168.232.60 "docker logs -f meme-detector"

# Estado / estadísticas
ssh pablo@192.168.232.60 "docker exec meme-detector python -c 'import json;d=json.load(open(\"/data/status.json\"));print(json.dumps({k:d[k] for k in (\"embudo\",\"estadisticas\")},indent=1))'"

# Historial de avisos
ssh pablo@192.168.232.60 "docker exec meme-detector cat /data/alerts.csv"

# Deploy — git push + pull + rebuild, NUNCA scp
cd /home/calvop/meme-detector && git push origin main
ssh pablo@192.168.232.60 "cd /opt/stacks/meme-detector && git pull && docker compose up -d --build"
```

Cambiar un umbral: editar `.env` en el server y `docker compose up -d` (no `restart`: no relee `.env`).

## Arquitectura

| Archivo | Rol |
|---|---|
| `config.py` | Umbrales vía `os.getenv()` |
| `fuentes.py` | Clientes GeckoTerminal / DexScreener / RugCheck / CoinGecko con throttle por API |
| `engine.py` | Filtros, snapshots, cierre de horizontes con velas, estadísticas |
| `db.py` | SQLite: `watch` (lista de observación + motivo de descarte), `alerts` (avisos + resultados), `state` |
| `bot.py` | Loop, Telegram (solo envío), `alerts.csv`, `status.json`, heartbeat |

## Pendiente

- ~~Integrar al dashboard de cryptobot~~ hecho 30/10/2026: pestaña **Memecoins**, tarjeta Bot 8 en Comparación y tarjeta en Resumen (color `--pink`). El dashboard monta `memedetector_data` read-only en `/memes_data` y expone `/api/memes/status` y `/api/memes/daily`. Si cambia el formato de `status.json`/`alerts.csv`, revisar `dashboard.py` de cryptobot.
- Llevar los avisos al signal-auditor como fuente, con GeckoTerminal como venue nueva.
- Calibrar umbrales con el embudo de `status.json` después de 3-4 semanas.
