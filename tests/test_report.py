"""The `kairos digest` text builder — robust on empty data, rich when populated."""
from kairos.data import store
from kairos.report import digest_text


def _vf(venue, asset, basis, fapr, poll_ts=1000):
    return {"venue": venue, "symbol": f"{venue}-{asset}", "asset": asset, "poll_ts": poll_ts,
            "funding_rate": 0.0, "interval_hours": 8.0, "funding_apr": fapr, "interest_rate": 0.0,
            "mark": None, "index_price": None, "basis_bps": basis,
            "open_interest": None, "next_funding_time": None}


def test_digest_runs_on_empty_db(tmp_path):
    txt = digest_text(store.connect(tmp_path / "t.db"))
    assert "KAIROS digest" in txt
    assert "forward edge: BTC 0/50" in txt   # progress line always present


def test_digest_has_venue_health_and_extremes(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.insert_venue_funding(conn, [_vf("kalshi", "BTC", 1.3, 0.0), _vf("okx", "BTC", -4.7, 0.044)])
    store.insert_snapshot(conn, {"symbol": "KXDOGEPERP", "poll_ts": 1000, "funding_est": 0.0005})
    store.insert_snapshot(conn, {"symbol": "KXXRPPERP", "poll_ts": 1000, "funding_est": -0.0003})
    txt = digest_text(conn)
    assert "venue health" in txt
    assert "kalshi" in txt and "okx" in txt          # both responded
    assert "hyperliquid" in txt and "✗ no data" in txt  # missing venue flagged
    assert "Kalshi funding x-section" in txt
    assert "DOGE" in txt and "XRP" in txt             # richest / cheapest
