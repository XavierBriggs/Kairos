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


def test_coingecko_normalizes_binance_bybit(monkeypatch):
    sample = [
        {"market": "Binance (Futures)", "symbol": "BTCUSDT", "contract_type": "perpetual",
         "funding_rate": 0.004345, "price": "63955.0", "index": 63983.35, "open_interest": 6.3e9},
        {"market": "Bybit (Futures)", "symbol": "BTCUSDT", "contract_type": "perpetual",
         "funding_rate": 0.007846, "price": "63932.3", "index": 64029.59, "open_interest": 3.3e9},
        {"market": "Binance (Futures)", "symbol": "ETHUSDT", "contract_type": "perpetual",
         "funding_rate": 0.003208, "price": "1734.0", "index": 1735.73, "open_interest": 3.8e9},
        {"market": "Other (Futures)", "symbol": "BTCUSDT", "contract_type": "perpetual",
         "funding_rate": 9, "price": "1", "index": 1},   # not Binance/Bybit -> ignored
    ]
    monkeypatch.setattr(offshore, "_get", lambda url, params=None: sample)
    out = offshore.coingecko_perps(["BTC", "ETH"])
    b = out[("binance", "BTC")]
    assert b["venue"] == "binance" and b["interval_hours"] == 8.0
    assert abs(b["funding_rate"] - 0.004345 / 100) < 1e-12               # PERCENT -> fraction
    assert abs(b["basis_bps"] - ((63955.0 - 63983.35) / 63983.35 * 1e4)) < 1e-3  # ~ -4.43 bp
    assert b["funding_apr"] is not None and b["interest_rate"] == offshore.INTEREST_8H
    assert abs(out[("bybit", "BTC")]["funding_rate"] - 0.007846 / 100) < 1e-12
    assert ("binance", "ETH") in out
    assert ("binance", "SOL") not in out                                 # not present in feed


def test_dead_venue_returns_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(offshore, "_get", boom)
    assert offshore.okx_live("BTC-USDT-SWAP") is None       # never raises -> round survives
    assert offshore.gate_history("BTC_USDT") == []
    assert offshore.coingecko_perps(["BTC"]) == {}          # vendor down -> empty, round survives
