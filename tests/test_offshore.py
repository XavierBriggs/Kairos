"""Offshore adapters — normalization + annualization, no network (monkeypatched HTTP)."""
import pytest

from kairos.data import offshore


def test_annualize_by_interval():
    assert offshore.annualize_funding(0.0001, 8) == pytest.approx(0.0001 * 365 * 24 / 8)
    assert offshore.annualize_funding(0.0001, 1) == pytest.approx(0.0001 * 8760)  # hourly venue
    assert offshore.annualize_funding(None, 8) is None


def test_binance_live_normalizes(monkeypatch):
    def fake_get(url, params=None):
        if "premiumIndex" in url:
            return {"lastFundingRate": "0.0001", "markPrice": "60010", "indexPrice": "60000",
                    "nextFundingTime": 123}
        return {"openInterest": "1000"}
    monkeypatch.setattr(offshore, "_get", fake_get)
    r = offshore.binance_live("BTCUSDT")
    assert r["venue"] == "binance" and r["interval_hours"] == 8.0
    assert abs(r["funding_rate"] - 0.0001) < 1e-12
    assert abs(r["basis_bps"] - (10 / 60000 * 1e4)) < 1e-6  # (mark-index)/index
    assert r["open_interest"] == 1000 and r["funding_apr"] > 0


def test_hyperliquid_all_is_hourly(monkeypatch):
    def fake_post(url, body):
        return [{"universe": [{"name": "BTC"}]},
                [{"funding": "0.00001", "markPx": "60010", "oraclePx": "60000", "openInterest": "5"}]]
    monkeypatch.setattr(offshore, "_post", fake_post)
    d = offshore.hyperliquid_all()
    assert d["BTC"]["interval_hours"] == 1.0
    assert abs(d["BTC"]["funding_rate"] - 0.00001) < 1e-12


def test_okx_captures_premium_and_live_interest(monkeypatch):
    def fake_get(url, params=None):
        return {"data": [{"fundingRate": "0.0000389", "premium": "-0.00047",
                          "interestRate": "0.0001", "nextFundingTime": "1"}]}
    monkeypatch.setattr(offshore, "_get", fake_get)
    r = offshore.okx_live("BTC-USDT-SWAP")
    assert r["venue"] == "okx" and r["interval_hours"] == 8.0
    assert abs(r["basis_bps"] - (-0.00047 * 1e4)) < 1e-6     # premium directly -> basis
    assert abs(r["interest_rate"] - 0.0001) < 1e-12          # baseline sourced LIVE from OKX


def test_gate_uses_realized_funding_not_indicative(monkeypatch):
    def fake_get(url, params=None):
        return [{"last": "62750", "index_price": "62791", "funding_rate": "0.000051",
                 "funding_rate_indicative": "0.000099"}]   # must ignore indicative
    monkeypatch.setattr(offshore, "_get", fake_get)
    r = offshore.gate_live("BTC_USDT")
    assert abs(r["funding_rate"] - 0.000051) < 1e-12       # realized, not indicative
    assert r["basis_bps"] is not None


def test_dead_venue_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(offshore, "_get", boom)
    assert offshore.okx_live("BTC-USDT-SWAP") is None       # never raises -> round survives
    assert offshore.gate_history("BTC_USDT") == []
