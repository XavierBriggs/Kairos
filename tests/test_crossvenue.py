"""Cross-venue asset map + dispersion readout."""
import time

from kairos.crossvenue import ASSET_MAP, dispersion, index_offset_daily
from kairos.data import store


def test_asset_map_has_all_venues():
    for asset, m in ASSET_MAP.items():
        assert {"kalshi", "binance", "bybit", "hyperliquid"} <= set(m), asset


def _vf(venue, apr, basis_bps, interest_rate, poll_ts=1, interval_hours=8.0):
    ipy = 365 * 24 / interval_hours
    return {"venue": venue, "symbol": f"{venue}-BTC", "asset": "BTC", "poll_ts": poll_ts,
            "funding_rate": apr / ipy, "interval_hours": interval_hours, "funding_apr": apr,
            "interest_rate": interest_rate, "mark": None, "index_price": None,
            "basis_bps": basis_bps, "open_interest": None, "next_funding_time": None}


def test_dispersion_decomposes_funding_into_baseline_and_premium(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    # hyperliquid funding 11% but it's almost ALL the hourly interest baseline -> premium ~0.
    # kalshi funding 2% with NO baseline -> premium 2%. The honest signal is the PREMIUM.
    store.insert_venue_funding(conn, [
        _vf("kalshi", 0.02, basis_bps=3.0, interest_rate=0.0),
        _vf("hyperliquid", 0.11, basis_bps=0.1, interest_rate=0.0000125, interval_hours=1.0),
    ])
    df = dispersion(conn, "BTC").set_index("venue")
    assert {"baseline_apr_%", "premium_apr_%"}.issubset(df.reset_index().columns)
    # HL baseline ~10.95% (0.0000125*8760); premium = 11 - 10.95 ~ 0
    assert abs(df.loc["hyperliquid", "baseline_apr_%"] - 10.95) < 0.1
    assert abs(df.loc["hyperliquid", "premium_apr_%"]) < 0.2
    # kalshi: no baseline -> premium == funding == 2%
    assert abs(df.loc["kalshi", "premium_apr_%"] - 2.0) < 1e-6
    # sorted by premium (the edge): kalshi (2%) ranks above hyperliquid (~0%)
    assert dispersion(conn, "BTC")["venue"].iloc[0] == "kalshi"


def test_dispersion_uses_latest_poll(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    store.insert_venue_funding(conn, [_vf("okx", 0.05, 4.0, 0.0001, poll_ts=1),
                                      _vf("okx", 0.09, 8.0, 0.0001, poll_ts=2)])
    df = dispersion(conn, "BTC")
    assert len(df) == 1 and abs(df["funding_apr_%"].iloc[0] - 9.0) < 1e-6  # newest only


def _vfo(venue, basis_bps, poll_ts, asset="XRP"):
    return {"venue": venue, "symbol": f"{venue}-{asset}", "asset": asset, "poll_ts": poll_ts,
            "funding_rate": 0.0, "interval_hours": 8.0, "funding_apr": 0.0, "interest_rate": 0.0,
            "mark": None, "index_price": None, "basis_bps": basis_bps,
            "open_interest": None, "next_funding_time": None}


def test_index_offset_isolates_kalshi_and_excludes_vendor_venues(tmp_path):
    conn = store.connect(tmp_path / "t.db")
    now_ms = int(time.time() * 1000)
    day = 86_400_000
    rows = []
    for d in (2, 1):  # two distinct days within the window
        ts = now_ms - d * day
        rows += [
            _vfo("kalshi", 3.0, ts),         # Kalshi basis +3bp
            _vfo("hyperliquid", -5.0, ts),   # clean offshore ~ -5bp
            _vfo("okx", -5.0, ts),
            _vfo("binance", -50.0, ts),      # CoinGecko-vendored noise -> MUST be excluded
        ]
    store.insert_venue_funding(conn, rows)
    df = index_offset_daily(conn, "XRP", days=10)
    assert len(df) == 2                                   # one row per day
    # offset = kalshi(+3) - mean(hl,okx)=-5 = +8bp; binance -50 excluded (else it'd skew to ~ -20)
    assert abs(df["offshore_bps"].iloc[-1] - (-5.0)) < 0.01
    assert abs(df["offset_bps"].iloc[-1] - 8.0) < 0.01
