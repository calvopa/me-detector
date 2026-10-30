"""SQLite del detector: lista de observación + avisos con su seguimiento."""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS watch (
    mint       TEXT PRIMARY KEY,
    first_seen INTEGER NOT NULL,       -- ms UTC
    rc_ts      INTEGER,                -- último reporte de RugCheck
    motivo     TEXT                    -- por qué no pasó en la última evaluación
);
CREATE TABLE IF NOT EXISTS alerts (
    id         INTEGER PRIMARY KEY,
    mint       TEXT NOT NULL UNIQUE,
    symbol     TEXT,
    name       TEXT,
    ts         INTEGER NOT NULL,       -- ms UTC del aviso
    pool       TEXT NOT NULL,
    dex        TEXT,
    precio     REAL NOT NULL,
    liq        REAL,
    vol_h1     REAL,
    fdv        REAL,
    holders    INTEGER,
    top10      REAL,
    edad_min   REAL,
    precio_max REAL,
    precio_min REAL,
    ultimo     REAL,
    ultima_liq REAL,
    rug_ts     INTEGER,                -- cuándo la liquidez cayó bajo RUG_LIQ_FRAC
    r1h        REAL,                   -- variación % al cierre de cada horizonte
    r6h        REAL,
    r24h       REAL,
    r7d        REAL,
    max_pct    REAL,                   -- mejor momento desde el aviso, %
    min_pct    REAL                    -- peor momento desde el aviso, %
);
CREATE TABLE IF NOT EXISTS state (k TEXT PRIMARY KEY, v TEXT);
"""


def connect(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.executescript(SCHEMA)
    return con


def get_state(con, k, default=None):
    row = con.execute("SELECT v FROM state WHERE k=?", (k,)).fetchone()
    return json.loads(row["v"]) if row else default


def set_state(con, k, v):
    con.execute("INSERT INTO state(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v",
                (k, json.dumps(v)))
