"""SQLite store for forward capture (Phase C) — the proper DB.

Three append-only / idempotent tables: dense `snapshot` rows (mark/reference/basis +
top-of-book L2 + OI + funding estimate, every poll), official 8h `funding_settlement`
rows, and `poll_run` health audit. The derived `build_labeled_intervals` joins the two
into the leakage-safe canonical schema the backtest/convergence code already consume —
now with a TRUE independent basis that Kalshi history alone cannot provide.

Stdlib sqlite3 (no new dependency), WAL mode so db-status / forward can read while the
collector writes. Idempotent by construction: snapshot UNIQUE(symbol, poll_ts) with
INSERT OR IGNORE; settlements upserted by UNIQUE(symbol, funding_time).
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from ..basis import basis_tier
from ..config import FundingModelConfig, db_path

_SNAPSHOT_COLS = (
    "symbol", "poll_ts", "mark", "reference", "settlement_mark", "liquidation_mark",
    "last_price", "mark_ts", "reference_ts", "basis_bps", "best_bid", "best_ask",
    "spread_bps", "microprice", "imbalance_l1", "funding_est", "computed_time",
    "next_funding_time", "open_interest", "volume_24h", "leverage_estimate",
    "book_json", "raw_market", "raw_funding_est",
)

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    {', '.join(c + ' ' + ('TEXT' if c in ('symbol','computed_time','next_funding_time','book_json','raw_market','raw_funding_est') else 'INTEGER' if c in ('poll_ts','mark_ts','reference_ts') else 'REAL') for c in _SNAPSHOT_COLS)},
    UNIQUE(symbol, poll_ts)
);
CREATE INDEX IF NOT EXISTS idx_snapshot_symbol_ts ON snapshot(symbol, poll_ts);

CREATE TABLE IF NOT EXISTS funding_settlement (
    symbol TEXT NOT NULL,
    funding_time TEXT NOT NULL,
    funding_rate REAL,
    mark_price REAL,
    UNIQUE(symbol, funding_time)
);
CREATE INDEX IF NOT EXISTS idx_settle_symbol_time ON funding_settlement(symbol, funding_time);

CREATE TABLE IF NOT EXISTS poll_run (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_ts INTEGER NOT NULL,
    n_symbols INTEGER,
    n_ok INTEGER,
    n_err INTEGER,
    duration_ms INTEGER,
    halted INTEGER,
    errors TEXT
);

CREATE TABLE IF NOT EXISTS trade (
    symbol TEXT NOT NULL,
    trade_id TEXT NOT NULL,
    created_time TEXT,
    price REAL,
    count REAL,
    taker_side TEXT,
    UNIQUE(trade_id)
);
CREATE INDEX IF NOT EXISTS idx_trade_symbol_time ON trade(symbol, created_time);

CREATE TABLE IF NOT EXISTS candle (
    symbol TEXT NOT NULL,
    period INTEGER NOT NULL,
    end_ts INTEGER NOT NULL,
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, open_interest REAL,
    UNIQUE(symbol, period, end_ts)
);
CREATE INDEX IF NOT EXISTS idx_candle ON candle(symbol, period, end_ts);

-- Cross-venue live funding (Binance / Bybit / Hyperliquid / Kalshi), per poll.
CREATE TABLE IF NOT EXISTS venue_funding (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset TEXT,
    poll_ts INTEGER NOT NULL,
    funding_rate REAL,
    interval_hours REAL,
    funding_apr REAL,
    interest_rate REAL,
    mark REAL, index_price REAL, basis_bps REAL,
    open_interest REAL, next_funding_time TEXT,
    UNIQUE(venue, symbol, poll_ts)
);
CREATE INDEX IF NOT EXISTS idx_vf ON venue_funding(asset, venue, poll_ts);

-- Cross-venue HISTORICAL funding (offshore venues backfill years; enables immediate backtest).
CREATE TABLE IF NOT EXISTS venue_funding_hist (
    venue TEXT NOT NULL,
    symbol TEXT NOT NULL,
    asset TEXT,
    funding_time INTEGER NOT NULL,
    funding_rate REAL,
    interval_hours REAL,
    UNIQUE(venue, symbol, funding_time)
);
CREATE INDEX IF NOT EXISTS idx_vfh ON venue_funding_hist(asset, venue, funding_time);

-- WebSocket tick-level capture (Kalshi perps). The margin ticker channel carries
-- price/bid/ask/sizes (NOT funding or mark — those are REST-only via funding_estimate).
CREATE TABLE IF NOT EXISTS ws_tick (
    symbol TEXT NOT NULL,
    recv_ts INTEGER NOT NULL,
    price REAL, bid REAL, ask REAL, bid_size REAL, ask_size REAL,
    raw TEXT,
    UNIQUE(symbol, recv_ts)
);
CREATE INDEX IF NOT EXISTS idx_wstick ON ws_tick(symbol, recv_ts);

CREATE TABLE IF NOT EXISTS ws_book (
    symbol TEXT NOT NULL,
    recv_ts INTEGER NOT NULL,
    seq INTEGER, sid INTEGER,
    side TEXT, price REAL, delta REAL, is_snapshot INTEGER
);
CREATE INDEX IF NOT EXISTS idx_wsbook ON ws_book(symbol, recv_ts);

-- 1-minute rollup of ws_tick (top-of-book), kept INDEFINITELY as the compact long-term
-- archive after raw ws_tick/ws_book are pruned. Populated by the deploy prune job.
CREATE TABLE IF NOT EXISTS ws_tick_1m (
    symbol TEXT NOT NULL,
    minute_ts INTEGER NOT NULL,        -- epoch seconds, minute-floored
    n INTEGER,
    px_open REAL, px_high REAL, px_low REAL, px_close REAL,
    bid_mean REAL, ask_mean REAL, spread_bps_mean REAL, imbalance_mean REAL,
    UNIQUE(symbol, minute_ts)
);
CREATE INDEX IF NOT EXISTS idx_wstick1m ON ws_tick_1m(symbol, minute_ts);
"""


def connect(path: Path | str | None = None, autocommit: bool = False) -> sqlite3.Connection:
    p = Path(path) if path else db_path()
    new = not p.exists()
    p.parent.mkdir(parents=True, exist_ok=True)
    # autocommit (isolation_level=None) for the high-frequency WS stream: each write is its
    # own tiny transaction, so it never holds an open write lock across many inserts and
    # starve the 60s collector ("database is locked"). The collector keeps "" (deferred +
    # explicit commit). busy_timeout is generous because both writers share one DB.
    conn = sqlite3.connect(str(p), timeout=15.0, isolation_level=(None if autocommit else ""))
    conn.row_factory = sqlite3.Row
    # auto_vacuum must be set BEFORE the first table is created (fresh DB) to enable
    # incremental_vacuum reclaim after retention pruning.
    if new:
        conn.execute("PRAGMA auto_vacuum=INCREMENTAL")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=15000")
    init_schema(conn)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_SCHEMA)
    conn.commit()


def insert_snapshot(conn: sqlite3.Connection, row: dict) -> bool:
    """Insert one derived snapshot row. Returns True if a new row was written (False if
    a row for (symbol, poll_ts) already existed — idempotent)."""
    cols = ", ".join(_SNAPSHOT_COLS)
    ph = ", ".join(f":{c}" for c in _SNAPSHOT_COLS)
    vals = {c: row.get(c) for c in _SNAPSHOT_COLS}
    cur = conn.execute(f"INSERT OR IGNORE INTO snapshot ({cols}) VALUES ({ph})", vals)
    conn.commit()
    return cur.rowcount > 0


def upsert_settlements(conn: sqlite3.Connection, symbol: str, records: list[dict]) -> int:
    """Upsert official 8h funding settlements. Returns the count processed."""
    rows = [
        (symbol, r.get("funding_time"), _f(r.get("funding_rate")), _f(r.get("mark_price")))
        for r in records
        if r.get("funding_time")
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO funding_settlement (symbol, funding_time, funding_rate, mark_price) "
        "VALUES (?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    return len(rows)


def record_run(
    conn: sqlite3.Connection, run_ts: int, n_symbols: int, n_ok: int, n_err: int,
    duration_ms: int, errors: str = "", halted: int | None = None,
) -> None:
    conn.execute(
        "INSERT INTO poll_run (run_ts, n_symbols, n_ok, n_err, duration_ms, errors, halted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_ts, n_symbols, n_ok, n_err, duration_ms, errors[:2000], halted),
    )
    conn.commit()


def insert_trades(conn: sqlite3.Connection, symbol: str, records: list[dict]) -> int:
    """Insert Kalshi perp prints (the taker tape); idempotent by trade_id. Returns new rows."""
    rows = [
        (symbol, r.get("trade_id"), r.get("created_time"), _f(r.get("price")),
         _f(r.get("count")), r.get("taker_side"))
        for r in records if r.get("trade_id")
    ]
    cur = conn.executemany(
        "INSERT OR IGNORE INTO trade (symbol, trade_id, created_time, price, count, taker_side) "
        "VALUES (?, ?, ?, ?, ?, ?)", rows,
    )
    conn.commit()
    return cur.rowcount


def insert_candles(conn: sqlite3.Connection, symbol: str, period: int, records: list[dict]) -> int:
    """Insert OHLCV+OI candles; idempotent by (symbol, period, end_ts). Returns new rows."""
    def px(r, k):
        p = r.get("price") or {}
        return _f(p.get(k)) if isinstance(p, dict) else None
    rows = [
        (symbol, period, r.get("end_period_ts"), px(r, "open"), px(r, "high"),
         px(r, "low"), px(r, "close"), _f(r.get("volume")), _f(r.get("open_interest")))
        for r in records if r.get("end_period_ts") is not None
    ]
    cur = conn.executemany(
        "INSERT OR IGNORE INTO candle (symbol, period, end_ts, open, high, low, close, volume, open_interest) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows,
    )
    conn.commit()
    return cur.rowcount


def insert_venue_funding(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Insert cross-venue live funding rows (one per venue/symbol/poll). Idempotent."""
    cols = ("venue", "symbol", "asset", "poll_ts", "funding_rate", "interval_hours",
            "funding_apr", "interest_rate", "mark", "index_price", "basis_bps",
            "open_interest", "next_funding_time")
    vals = [tuple(r.get(c) for c in cols) for r in rows]
    cur = conn.executemany(
        f"INSERT OR IGNORE INTO venue_funding ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})", vals,
    )
    conn.commit()
    return cur.rowcount


def upsert_venue_funding_hist(conn: sqlite3.Connection, rows: list[dict]) -> int:
    """Upsert offshore historical funding (venue, symbol, funding_time UNIQUE)."""
    cols = ("venue", "symbol", "asset", "funding_time", "funding_rate", "interval_hours")
    vals = [tuple(r.get(c) for c in cols) for r in rows]
    conn.executemany(
        f"INSERT OR REPLACE INTO venue_funding_hist ({', '.join(cols)}) "
        f"VALUES ({', '.join('?' for _ in cols)})", vals,
    )
    conn.commit()
    return len(vals)


def insert_ws_tick(conn: sqlite3.Connection, row: dict) -> bool:
    cur = conn.execute(
        "INSERT OR IGNORE INTO ws_tick (symbol, recv_ts, price, bid, ask, bid_size, ask_size, raw) "
        "VALUES (:symbol, :recv_ts, :price, :bid, :ask, :bid_size, :ask_size, :raw)", row,
    )
    return cur.rowcount > 0


def insert_ws_book(conn: sqlite3.Connection, rows: list[dict]) -> int:
    cols = ("symbol", "recv_ts", "seq", "sid", "side", "price", "delta", "is_snapshot")
    vals = [tuple(r.get(c) for c in cols) for r in rows]
    conn.executemany(
        f"INSERT INTO ws_book ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})", vals,
    )
    return len(vals)


def _f(x) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def load_venue_funding(conn: sqlite3.Connection, asset: str) -> pd.DataFrame:
    """Latest live cross-venue funding rows for an asset (for the dispersion readout)."""
    return pd.read_sql_query(
        "SELECT * FROM venue_funding WHERE asset = ? ORDER BY poll_ts", conn, params=(asset,)
    )


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Row count per table — the high-level db-status header."""
    tables = ("snapshot", "funding_settlement", "trade", "candle", "venue_funding",
              "venue_funding_hist", "ws_tick", "ws_book", "poll_run")
    out = {}
    for t in tables:
        try:
            out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        except sqlite3.OperationalError:
            out[t] = 0
    return out


def coverage(conn: sqlite3.Connection) -> pd.DataFrame:
    """Per-symbol snapshot count, time span, and settlement count — the db-status view."""
    return pd.read_sql_query(
        """
        SELECT s.symbol,
               COUNT(*)                         AS snapshots,
               MIN(s.poll_ts)                   AS first_poll_ms,
               MAX(s.poll_ts)                   AS last_poll_ms,
               (SELECT COUNT(*) FROM funding_settlement f WHERE f.symbol = s.symbol) AS settlements
        FROM snapshot s GROUP BY s.symbol ORDER BY s.symbol
        """,
        conn,
    )


def load_snapshots(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM snapshot WHERE symbol = ? ORDER BY poll_ts", conn, params=(symbol,)
    )


def load_settlements(conn: sqlite3.Connection, symbol: str) -> pd.DataFrame:
    df = pd.read_sql_query(
        "SELECT * FROM funding_settlement WHERE symbol = ? ORDER BY funding_time",
        conn, params=(symbol,),
    )
    if not df.empty:
        df["t"] = pd.to_datetime(df["funding_time"], utc=True)
    return df


def build_labeled_intervals(
    conn: sqlite3.Connection, symbol: str, cfg: FundingModelConfig | None = None
) -> pd.DataFrame:
    """Join settlements + snapshots into the leakage-safe canonical schema.

    For each settlement at T (after the first): funding_next = rate at T (label),
    funding_now = rate at the previous settlement, and basis_bps = the basis from the
    last snapshot STRICTLY BEFORE T within the window (plus basis_twap over the window).
    Rows with no pre-settlement snapshot are dropped (the independent basis is the whole
    point — we can't test the nowcast without it). Returns the same columns the backtest
    and convergence.forward_edge consume.
    """
    cfg = cfg or FundingModelConfig()
    settle = load_settlements(conn, symbol)
    snaps = load_snapshots(conn, symbol)
    if len(settle) < 2 or snaps.empty:
        return pd.DataFrame()
    snaps = snaps.dropna(subset=["basis_bps"]).copy()
    snaps["t"] = pd.to_datetime(snaps["poll_ts"], unit="ms", utc=True)

    rows = []
    for i in range(1, len(settle)):
        t = settle["t"].iloc[i]
        t_prev = settle["t"].iloc[i - 1]
        window = snaps[(snaps["t"] >= t_prev) & (snaps["t"] < t)]
        if window.empty:
            continue
        asof = window.iloc[-1]  # last poll strictly before settlement T
        interval_h = max(int(round((t - t_prev).total_seconds() / 3600.0)), 1)
        b = float(asof["basis_bps"])
        rows.append(
            {
                "ts": t,
                "venue": "kalshi",
                "symbol": symbol,
                "interval_hours": interval_h,
                "funding_now": float(settle["funding_rate"].iloc[i - 1]),
                "mark": float(asof["mark"]) if pd.notna(asof["mark"]) else float("nan"),
                "reference": float(asof["reference"]) if pd.notna(asof["reference"]) else float("nan"),
                "basis_bps": b,
                "basis_twap_bps": float(window["basis_bps"].mean()),
                "funding_next": float(settle["funding_rate"].iloc[i]),
                "regime": "stressed" if abs(b) > 20 else "calm",
                "basis_tier": basis_tier(b),
            }
        )
    return pd.DataFrame(rows)
