"""Read-only offshore perp funding adapters.

US-REACHABLE, keyless venues (verified live 2026-06-19): OKX, Bitget, Gate, Hyperliquid.
Binance and Bybit are kept but **geo-block US connections** (451 / 403) — usable only from
a non-US host or via a vendor, so they are NOT in the default cross-venue set.

Each `*_live` returns a normalized dict; `*_history` returns normalized rows (offshore
venues keep months/years, so cross-venue carry/RV is backtestable now). `basis_bps`
(= premium = (mark−index)/index) is captured per venue so the cross-venue signal can be
normalized to its PREMIUM-driven component — funding rates themselves are NOT comparable
across venues because each adds a different interest baseline / dead-zone / clamp.

Normalized live shape:
  {venue, symbol, funding_rate, interval_hours, funding_apr, mark, index_price,
   basis_bps, open_interest, next_funding_time}
A dead venue returns None — never kills a collection round.
"""
from __future__ import annotations

from typing import Any

import requests

_SESSION = requests.Session()
_TIMEOUT = 15.0
_HOURS_PER_YEAR = 365.0 * 24.0


def annualize_funding(rate: float | None, interval_hours: float) -> float | None:
    if rate is None or interval_hours <= 0:
        return None
    return rate * (_HOURS_PER_YEAR / interval_hours)


def _f(x: Any) -> float | None:
    if x is None or x == "":
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _get(url: str, params: dict | None = None) -> Any:
    r = _SESSION.get(url, params=params, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(url: str, body: dict) -> Any:
    r = _SESSION.post(url, json=body, timeout=_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _basis_bps(mark: float | None, index: float | None) -> float | None:
    if mark is None or index is None or index == 0:
        return None
    return (mark - index) / index * 1e4


# Standard CEX interest-rate baseline (~0.01% per 8h ≈ 11%/yr). OKX provides it live;
# Bitget/Gate/Binance/Bybit follow the same convention (assumed); Hyperliquid splits it
# hourly (0.01%/8h = 0.0000125/hr); Kalshi has NO interest baseline (premium-only).
INTEREST_8H = 0.0001
INTEREST_1H = 0.0000125


def _norm(venue, symbol, rate, interval_h, mark, index, oi, next_ft, basis_bps=None,
          interest_rate=None) -> dict:
    return {
        "venue": venue, "symbol": symbol, "funding_rate": rate, "interval_hours": interval_h,
        "funding_apr": annualize_funding(rate, interval_h), "mark": mark, "index_price": index,
        "basis_bps": basis_bps if basis_bps is not None else _basis_bps(mark, index),
        "open_interest": oi, "next_funding_time": next_ft, "interest_rate": interest_rate,
    }


# --- OKX (US-reachable; gives premium directly) -----------------------------
_OKX = "https://www.okx.com"


def okx_live(inst: str = "BTC-USDT-SWAP") -> dict | None:
    try:
        d = _get(f"{_OKX}/api/v5/public/funding-rate", {"instId": inst})["data"][0]
    except Exception:  # noqa: BLE001
        return None
    rate, premium = _f(d.get("fundingRate")), _f(d.get("premium"))
    return _norm("okx", inst, rate, 8.0, None, None, None, d.get("nextFundingTime"),
                 basis_bps=premium * 1e4 if premium is not None else None,
                 interest_rate=_f(d.get("interestRate")))   # OKX gives the baseline live


def okx_history(inst: str = "BTC-USDT-SWAP", limit: int = 100) -> list[dict]:
    try:
        rows = _get(f"{_OKX}/api/v5/public/funding-rate-history", {"instId": inst, "limit": limit})["data"]
    except Exception:  # noqa: BLE001
        return []
    return [{"venue": "okx", "symbol": inst, "funding_time": int(_f(r.get("fundingTime")) or 0),
             "funding_rate": _f(r.get("realizedRate") or r.get("fundingRate")), "interval_hours": 8.0}
            for r in rows]


# --- Bitget (US-reachable) --------------------------------------------------
_BITGET = "https://api.bitget.com"


def bitget_live(symbol: str = "BTCUSDT") -> dict | None:
    try:
        d = _get(f"{_BITGET}/api/v2/mix/market/ticker",
                 {"symbol": symbol, "productType": "usdt-futures"})["data"][0]
    except Exception:  # noqa: BLE001
        return None
    return _norm("bitget", symbol, _f(d.get("fundingRate")), 8.0,
                 _f(d.get("lastPr")), _f(d.get("indexPrice")), _f(d.get("holdingAmount")), None,
                 interest_rate=INTEREST_8H)   # assumed standard convention


def bitget_history(symbol: str = "BTCUSDT", limit: int = 100) -> list[dict]:
    try:
        rows = _get(f"{_BITGET}/api/v2/mix/market/history-fund-rate",
                    {"symbol": symbol, "productType": "usdt-futures", "pageSize": limit})["data"]
    except Exception:  # noqa: BLE001
        return []
    return [{"venue": "bitget", "symbol": symbol, "funding_time": int(_f(r.get("fundingTime")) or 0),
             "funding_rate": _f(r.get("fundingRate")), "interval_hours": 8.0} for r in rows]


# --- Gate (US-reachable; use REALIZED funding_rate, not indicative) ----------
_GATE = "https://api.gateio.ws"


def gate_live(contract: str = "BTC_USDT") -> dict | None:
    try:
        d = _get(f"{_GATE}/api/v4/futures/usdt/tickers", {"contract": contract})[0]
    except Exception:  # noqa: BLE001
        return None
    return _norm("gate", contract, _f(d.get("funding_rate")), 8.0,
                 _f(d.get("last")), _f(d.get("index_price")), None, None,
                 interest_rate=INTEREST_8H)   # assumed standard convention


def gate_history(contract: str = "BTC_USDT", limit: int = 100) -> list[dict]:
    try:
        rows = _get(f"{_GATE}/api/v4/futures/usdt/funding_rate", {"contract": contract, "limit": limit})
    except Exception:  # noqa: BLE001
        return []
    return [{"venue": "gate", "symbol": contract, "funding_time": int((_f(r.get("t")) or 0) * 1000),
             "funding_rate": _f(r.get("r")), "interval_hours": 8.0} for r in rows]


# --- Hyperliquid (US-reachable; HOURLY funding) -----------------------------
_HL = "https://api.hyperliquid.xyz"


def hyperliquid_all() -> dict[str, dict]:
    """All perps in ONE call: {coin: normalized live dict}. Hyperliquid funding is hourly."""
    try:
        meta, ctxs = _post(f"{_HL}/info", {"type": "metaAndAssetCtxs"})
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, dict] = {}
    for u, c in zip(meta.get("universe", []), ctxs):
        out[u.get("name")] = _norm("hyperliquid", u.get("name"), _f(c.get("funding")), 1.0,
                                   _f(c.get("markPx")), _f(c.get("oraclePx")), _f(c.get("openInterest")), None,
                                   interest_rate=INTEREST_1H)   # 0.01%/8h split hourly
    return out


def hyperliquid_live(coin: str = "BTC") -> dict | None:
    return hyperliquid_all().get(coin)


def hyperliquid_history(coin: str = "BTC", start_ms: int = 0) -> list[dict]:
    try:
        rows = _post(f"{_HL}/info", {"type": "fundingHistory", "coin": coin, "startTime": start_ms})
    except Exception:  # noqa: BLE001
        return []
    return [{"venue": "hyperliquid", "symbol": coin, "funding_time": r.get("time"),
             "funding_rate": _f(r.get("fundingRate")), "interval_hours": 1.0} for r in rows]


# --- Binance / Bybit: kept for non-US hosts; geo-block US (451 / 403) --------
_BINANCE = "https://fapi.binance.com"
_BYBIT = "https://api.bybit.com"


def binance_live(symbol: str = "BTCUSDT", interval_hours: float = 8.0) -> dict | None:
    """US-geo-blocked (451). Works only from a non-US host."""
    try:
        pi = _get(f"{_BINANCE}/fapi/v1/premiumIndex", {"symbol": symbol})
        oi = _get(f"{_BINANCE}/fapi/v1/openInterest", {"symbol": symbol})
    except Exception:  # noqa: BLE001
        return None
    return _norm("binance", symbol, _f(pi.get("lastFundingRate")), interval_hours,
                 _f(pi.get("markPrice")), _f(pi.get("indexPrice")), _f(oi.get("openInterest")),
                 pi.get("nextFundingTime"), interest_rate=INTEREST_8H)


def binance_history(symbol: str = "BTCUSDT", limit: int = 1000, interval_hours: float = 8.0) -> list[dict]:
    try:
        rows = _get(f"{_BINANCE}/fapi/v1/fundingRate", {"symbol": symbol, "limit": limit})
    except Exception:  # noqa: BLE001
        return []
    return [{"venue": "binance", "symbol": symbol, "funding_time": r.get("fundingTime"),
             "funding_rate": _f(r.get("fundingRate")), "interval_hours": interval_hours} for r in rows]


def bybit_live(symbol: str = "BTCUSDT", interval_hours: float = 8.0) -> dict | None:
    """US-geo-blocked (403). Works only from a non-US host."""
    try:
        t = (_get(f"{_BYBIT}/v5/market/tickers",
                  {"category": "linear", "symbol": symbol})["result"]["list"] or [{}])[0]
    except Exception:  # noqa: BLE001
        return None
    return _norm("bybit", symbol, _f(t.get("fundingRate")), interval_hours,
                 _f(t.get("markPrice")), _f(t.get("indexPrice")), _f(t.get("openInterest")),
                 t.get("nextFundingTime"), interest_rate=INTEREST_8H)


def bybit_history(symbol: str = "BTCUSDT", limit: int = 200, interval_hours: float = 8.0) -> list[dict]:
    try:
        rows = _get(f"{_BYBIT}/v5/market/funding/history",
                    {"category": "linear", "symbol": symbol, "limit": limit})["result"]["list"]
    except Exception:  # noqa: BLE001
        return []
    return [{"venue": "bybit", "symbol": symbol,
             "funding_time": int(_f(r.get("fundingRateTimestamp")) or 0),
             "funding_rate": _f(r.get("fundingRate")), "interval_hours": interval_hours} for r in rows]


# --- CoinGecko vendor: Binance + Bybit funding (their DIRECT APIs geo-block US) ------
# Keyless, US-reachable; one /derivatives call returns current funding for every venue.
# CoinGecko's funding_rate is in PERCENT/8h (so /100 -> fraction); basis is recomputed from
# price vs index for consistency with the other adapters. LIVE-only (no history endpoint here).
_CG = "https://api.coingecko.com/api/v3"
_CG_MARKET = {"binance": "Binance (Futures)", "bybit": "Bybit (Futures)"}


def coingecko_perps(assets: list[str]) -> dict[tuple[str, str], dict]:
    """Funding for Binance/Bybit USDT perps via CoinGecko. Returns {(venue, asset): normalized}."""
    try:
        data = _get(f"{_CG}/derivatives")
    except Exception:  # noqa: BLE001 - vendor down must not kill the round
        return {}
    want = {(_CG_MARKET[v], f"{a}USDT"): (v, a) for a in assets for v in _CG_MARKET}
    out: dict[tuple[str, str], dict] = {}
    for x in data:
        key = (x.get("market"), x.get("symbol"))
        if key in want and x.get("contract_type") == "perpetual":
            venue, asset = want[key]
            rate = _f(x.get("funding_rate"))
            out[(venue, asset)] = _norm(
                venue, f"{asset}USDT", rate / 100.0 if rate is not None else None, 8.0,
                _f(x.get("price")), _f(x.get("index")), _f(x.get("open_interest")), None,
                interest_rate=INTEREST_8H)
    return out
