"""Collector derive() + poll_once() against a FAKE in-process client (no network)."""
from kairos.collect import derive, poll_once
from kairos.config import CollectorConfig
from kairos.data import store


def _market(ticker="KXBTCPERP"):
    return {
        "ticker": ticker,
        "reference_price": {"price": "60000.0", "ts_ms": 111},
        "settlement_mark_price": {"price": "60030.0", "ts_ms": 112},
        "liquidation_mark_price": {"price": "60040.0", "ts_ms": 113},
        "price": "60025.0",
        "open_interest": "1234.00",
        "volume_24h": "5000.00",
        "leverage_estimate": 5.5,
    }


def _book():
    return {"orderbook": {
        "bids": [["59990.0", "10.0"], ["59995.0", "5.0"]],   # best bid = 59995 (size 5)
        "asks": [["60010.0", "8.0"], ["60005.0", "3.0"]],    # best ask = 60005 (size 3)
    }}


def _est():
    return {"funding_rate": 0.0001, "computed_time": "2026-06-18T19:45:00Z",
            "next_funding_time": "2026-06-18T20:00:00Z", "mark_price": "60030.0"}


def test_derive_computes_basis_and_microstructure():
    row = derive(_market(), _est(), _book(), poll_ts=1700, depth=10)
    assert abs(row["basis_bps"] - 5.0) < 1e-6          # 30/60000 = 5 bp
    assert row["best_bid"] == 59995.0                  # max bid price, not array position
    assert row["best_ask"] == 60005.0                  # min ask price
    assert abs(row["imbalance_l1"] - 5.0 / 8.0) < 1e-9
    assert row["spread_bps"] > 0 and row["microprice"] is not None
    assert "bids" in row["book_json"] and row["raw_market"]


class _FakeClient:
    """Minimal read-only Kalshi client stand-in — no network."""

    def markets(self, status=None):
        return [_market("KXBTCPERP"), _market("KXETHPERP")]

    def market(self, ticker):
        return _market(ticker)

    def funding_estimate(self, ticker):
        return _est()

    def orderbook(self, ticker, depth=10):
        return _book()

    def funding_history(self, ticker, start_ts=None, end_ts=None):
        return [{"funding_time": "2026-06-18T12:00:00Z", "funding_rate": 0.0001, "mark_price": "60030.0"}]

    def trades(self, ticker, limit=100):
        return [{"trade_id": f"{ticker}-1", "created_time": "t", "price": "60030.0",
                 "count": "3", "taker_side": "ask"}]


# capture_crossvenue off in unit tests (offshore live = network); trades use the fake above.
_CFG = CollectorConfig(depth=5, capture_crossvenue=False)


def test_poll_once_inserts_snapshots(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    n_ok, errs = poll_once(_FakeClient(), conn, _CFG)
    assert n_ok == 2 and not errs
    cov = store.coverage(conn)
    assert set(cov["symbol"]) == {"KXBTCPERP", "KXETHPERP"}
    assert int(cov["snapshots"].sum()) == 2
    # settlements were due (empty) -> upserted from funding_history
    assert int(cov["settlements"].sum()) == 2
    assert store.table_counts(conn)["trade"] == 2   # the taker tape captured


def test_poll_once_isolates_a_bad_symbol(tmp_path):
    conn = store.connect(tmp_path / "t.db")

    class _Flaky(_FakeClient):
        def orderbook(self, ticker, depth=10):
            if ticker == "KXETHPERP":
                raise RuntimeError("boom")
            return _book()

    n_ok, errs = poll_once(_Flaky(), conn, _CFG)
    assert n_ok == 1 and len(errs) == 1 and "KXETHPERP" in errs[0]
