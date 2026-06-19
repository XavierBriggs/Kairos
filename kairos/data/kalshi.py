"""READ-ONLY Kalshi perpetual-futures client (live wiring).

Reads market data and funding history for Kalshi crypto perps (KXBTCPERP, ...).
There are NO order/cancel/amend methods in this file — it is read-only by
construction, mirroring crates/fortuna-venues/src/kalshi/read_client.rs. Auth is
RSA-PSS request signing (semantically copied from crates/.../kalshi/auth.rs); the
market-data GETs are likely public, so the client signs when creds are present and
falls back to unsigned otherwise.

Endpoint/field detail comes from docs/research/venue/kinetics-perps-2026-06-10/.

Two schema adapters bridge to the backtest's canonical frame:
  - funding_history_to_schema: HISTORY -> rows. The independent index is NOT stored
    historically, so basis is reconstructed by de-clamping funding => the nowcast
    DEGENERATES to no_change on pure history (honest; that is why Phase C exists).
  - live_row: a forward snapshot (market mark + reference_price) -> a TRUE
    independent basis for the Phase-C convergence harness.
"""
from __future__ import annotations

import base64
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

from ..basis import basis_bps as _basis_bps
from ..basis import basis_tier, implied_basis_bps_from_funding
from ..config import REPO_ROOT, FundingModelConfig, kalshi_key_id, kalshi_private_key_path

_PROD_HOST = "https://external-api.kalshi.com"
_DEMO_HOST = "https://external-api.demo.kalshi.co"
_PREFIX = "/trade-api/v2"


class KalshiError(RuntimeError):
    pass


def _to_float(x: Any) -> float:
    """Kalshi rates are decimal STRINGS or numbers; funding-history marks are strings."""
    if x is None or x == "":
        return float("nan")
    return float(x)


def _price(field: Any) -> float:
    """Market-object price fields are nested objects {'price': '6.25', 'ts_ms': ...};
    funding-history marks are flat strings. Handle both."""
    if isinstance(field, dict):
        return _to_float(field.get("price"))
    return _to_float(field)


@dataclass
class _Signer:
    key_id: str
    private_key: Any  # cryptography private key object

    def headers(self, method: str, path: str) -> dict[str, str]:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        ts_ms = str(int(time.time() * 1000))
        msg = f"{ts_ms}{method.upper()}{path}".encode()
        sig = self.private_key.sign(
            msg,
            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=hashes.SHA256().digest_size),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.key_id,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode(),
            "KALSHI-ACCESS-TIMESTAMP": ts_ms,
        }


def _load_signer(demo: bool) -> _Signer | None:
    key_id = kalshi_key_id(demo)
    pem_path = kalshi_private_key_path(demo)
    if not key_id or not pem_path:
        return None
    p = Path(pem_path)
    if not p.is_absolute():
        p = REPO_ROOT / p
    if not p.exists():
        raise KalshiError(f"KALSHI private key not found at {p} (read-only client needs the PEM).")
    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    key = load_pem_private_key(p.read_bytes(), password=None)
    return _Signer(key_id=key_id, private_key=key)


@dataclass
class KalshiPerpClient:
    """Read-only Kalshi perps market-data client. Signs when creds are present."""

    demo: bool = False
    session: requests.Session = field(default_factory=requests.Session)
    timeout: float = 20.0
    max_retries: int = 4
    signer: _Signer | None = field(default=None)
    signed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self.host = _DEMO_HOST if self.demo else _PROD_HOST
        if self.signer is None:
            self.signer = _load_signer(self.demo)
        self.signed = self.signer is not None

    # --- transport ----------------------------------------------------------
    def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        path = f"{_PREFIX}{endpoint}"
        url = f"{self.host}{path}"
        params = {k: v for k, v in (params or {}).items() if v is not None}
        for attempt in range(self.max_retries):
            headers = self.signer.headers("GET", path) if self.signer else {}
            resp = self.session.get(url, params=params, headers=headers, timeout=self.timeout)
            if resp.status_code == 429:
                time.sleep(min(2.0 ** attempt * 0.5, 8.0))  # exp backoff; no Retry-After header
                continue
            if resp.status_code != 200:
                raise KalshiError(f"{resp.status_code} GET {endpoint}: {resp.text[:300]}")
            return resp.json()
        raise KalshiError(f"429 rate-limited after {self.max_retries} retries: GET {endpoint}")

    # --- read-only market data ---------------------------------------------
    def exchange_status(self) -> dict[str, Any]:
        return self._get("/margin/exchange/status")

    def markets(self, status: str | None = None) -> list[dict[str, Any]]:
        return self._get("/margin/markets", {"status": status}).get("markets", [])

    def market(self, ticker: str) -> dict[str, Any]:
        return self._get(f"/margin/markets/{ticker}").get("market", {})

    def orderbook(self, ticker: str, depth: int | None = None) -> dict[str, Any]:
        return self._get(f"/margin/markets/{ticker}/orderbook", {"depth": depth})

    def funding_estimate(self, ticker: str) -> dict[str, Any]:
        return self._get("/margin/funding_rates/estimate", {"ticker": ticker})

    def funding_history(
        self,
        ticker: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
        limit: int = 1000,
        max_pages: int = 50,
    ) -> list[dict[str, Any]]:
        """Cursor-paginated 8h funding history: [{funding_time, funding_rate, mark_price}]."""
        out: list[dict[str, Any]] = []
        cursor: str | None = None
        for _ in range(max_pages):
            page = self._get(
                "/margin/funding_rates/historical",
                {"ticker": ticker, "start_ts": start_ts, "end_ts": end_ts, "limit": limit, "cursor": cursor},
            )
            out.extend(page.get("funding_rates", []))
            cursor = page.get("cursor") or None
            if not cursor:
                break
        return out

    def trades(self, ticker: str, limit: int = 100) -> list[dict[str, Any]]:
        return self._get("/margin/trades", {"ticker": ticker, "limit": limit}).get("trades", [])

    def candlesticks(
        self, ticker: str, period_interval: int = 60, start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> list[dict[str, Any]]:
        """OHLCV + OI candles. period_interval in minutes (1 / 60 / 1440)."""
        return self._get(
            f"/margin/markets/{ticker}/candlesticks",
            {"period_interval": period_interval, "start_ts": start_ts, "end_ts": end_ts},
        ).get("candlesticks", [])

    def risk_parameters(self) -> dict[str, Any]:
        return self._get("/margin/risk_parameters")


# --- schema adapters --------------------------------------------------------
def funding_history_to_schema(
    records: list[dict[str, Any]], symbol: str, cfg: FundingModelConfig
) -> pd.DataFrame:
    """HISTORY -> canonical schema. reference is NaN (index not stored); basis is the
    de-clamped premium, so f_nowcast == no_change here BY CONSTRUCTION — Phase B
    validates funding autocorrelation + carry economics, not the independent basis."""
    if not records:
        raise KalshiError(f"no funding history returned for {symbol}")
    df = pd.DataFrame(records)
    df["ts"] = pd.to_datetime(df["funding_time"], utc=True)
    df = df.sort_values("ts").reset_index(drop=True)
    fn = df["funding_rate"].map(_to_float).to_numpy(float)
    mark = df["mark_price"].map(_to_float).to_numpy(float)
    # infer interval from the modal gap (never hard-code 8h)
    gaps_h = df["ts"].diff().dt.total_seconds().dropna() / 3600.0
    interval = int(round(gaps_h.median())) if len(gaps_h) else cfg.interval_hours
    b_bps = np.array([implied_basis_bps_from_funding(float(f), cfg) for f in fn])
    out = pd.DataFrame(
        {
            "ts": df["ts"],
            "venue": "kalshi",
            "symbol": symbol,
            "interval_hours": interval or cfg.interval_hours,
            "funding_now": fn,
            "mark": mark,
            "reference": np.nan,
            "basis_bps": b_bps,
            "funding_next": np.append(fn[1:], np.nan),
            "regime": ["stressed" if abs(x) > 3.0 else "calm" for x in b_bps],
            "basis_tier": [basis_tier(x) for x in b_bps],
        }
    )
    return out.iloc[:-1].reset_index(drop=True)  # drop last row (no next-funding label)


def live_row(market: dict[str, Any], funding_est: dict[str, Any], cfg: FundingModelConfig) -> dict[str, Any]:
    """A forward snapshot -> one canonical row with a TRUE independent basis
    (settlement_mark_price vs reference_price). Append these over time, then score
    with convergence.forward_edge once next-interval funding is known."""
    mark = _price(market.get("settlement_mark_price") or market.get("reference_price"))
    reference = _price(market.get("reference_price"))
    b_bps = _basis_bps(mark, reference) if reference and reference == reference else float("nan")
    return {
        "venue": "kalshi",
        "symbol": market.get("ticker"),
        "interval_hours": cfg.interval_hours,
        "mark": mark,
        "reference": reference,
        "basis_bps": b_bps,
        "funding_now": _to_float(funding_est.get("funding_rate")),
        "next_funding_time": funding_est.get("next_funding_time"),
    }
