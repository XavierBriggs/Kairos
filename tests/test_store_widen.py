"""New tables: trade / candle / venue_funding idempotency + counts."""
from kairos.data import store


def _conn(tmp_path):
    return store.connect(tmp_path / "t.db")


def test_trade_insert_is_idempotent_by_trade_id(tmp_path):
    conn = _conn(tmp_path)
    recs = [
        {"trade_id": "a", "created_time": "t0", "price": "6.25", "count": "5", "taker_side": "ask"},
        {"trade_id": "b", "created_time": "t1", "price": "6.26", "count": "2", "taker_side": "bid"},
    ]
    assert store.insert_trades(conn, "KXBTCPERP", recs) == 2
    assert store.insert_trades(conn, "KXBTCPERP", recs) == 0           # same ids ignored
    assert store.table_counts(conn)["trade"] == 2


def test_candle_insert_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    recs = [{"end_period_ts": 1000, "price": {"open": "1", "high": "2", "low": "0.5", "close": "1.5"},
             "volume": "100", "open_interest": "9"}]
    assert store.insert_candles(conn, "KXBTCPERP", 60, recs) == 1
    assert store.insert_candles(conn, "KXBTCPERP", 60, recs) == 0      # same (symbol,period,end_ts)
    assert store.table_counts(conn)["candle"] == 1


def test_venue_funding_idempotent(tmp_path):
    conn = _conn(tmp_path)
    row = {"venue": "binance", "symbol": "BTCUSDT", "asset": "BTC", "poll_ts": 1,
           "funding_rate": 0.0001, "interval_hours": 8.0, "funding_apr": 0.1}
    assert store.insert_venue_funding(conn, [row]) == 1
    assert store.insert_venue_funding(conn, [row]) == 0               # same (venue,symbol,poll_ts)
    assert store.table_counts(conn)["venue_funding"] == 1


def test_venue_funding_hist_upsert(tmp_path):
    conn = _conn(tmp_path)
    rows = [{"venue": "binance", "symbol": "BTCUSDT", "asset": "BTC",
             "funding_time": 1000, "funding_rate": 0.0001, "interval_hours": 8.0}]
    store.upsert_venue_funding_hist(conn, rows)
    store.upsert_venue_funding_hist(conn, rows)                       # idempotent
    assert store.table_counts(conn)["venue_funding_hist"] == 1
