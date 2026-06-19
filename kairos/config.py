"""Paths and parameters. Live Kalshi creds come from the repo-root fortuna/.env.

The Phase-A backtest is fully synthetic and needs no creds. Live read-only Kalshi
reads (Phase B/C) load `KALSHI_API_KEY_ID` + the PEM at `KALSHI_PRIVATE_KEY_PATH`
from `<repo>/.env` (or the demo pair). Secrets ONLY via env / those files, never
committed, never logged.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

# docs/kairos/kairos/config.py -> repo root is three parents up; project dir is one up.
REPO_ROOT = Path(__file__).resolve().parents[3]
_PROJECT_DIR = Path(__file__).resolve().parents[1]
# load in order; load_dotenv does NOT override already-set vars, so first wins.
# The repo-root .env holds the shared KALSHI_* creds (the operator put them there).
for _candidate in (
    _PROJECT_DIR / ".env.kairos",
    _PROJECT_DIR / ".env",
    REPO_ROOT / ".env",
):
    load_dotenv(_candidate)  # all gitignored; harmless if absent


def data_dir() -> Path:
    d = os.environ.get("KAIROS_DATA_DIR")
    return Path(d) if d else REPO_ROOT / "data" / "kairos"


def db_path() -> Path:
    """SQLite store for the forward collector (env-overridable via KAIROS_DB_PATH)."""
    p = os.environ.get("KAIROS_DB_PATH")
    return Path(p) if p else data_dir() / "kairos.db"


# --- Kalshi live read-only creds (resolved at client construction) -------------
def kalshi_key_id(demo: bool = False) -> str | None:
    return os.environ.get("KALSHI_API_DEMO_KEY_ID" if demo else "KALSHI_API_KEY_ID")


def kalshi_private_key_path(demo: bool = False) -> str | None:
    return os.environ.get(
        "KALSHI_DEMO_PRIVATE_KEY_PATH" if demo else "KALSHI_PRIVATE_KEY_PATH"
    )


@dataclass(frozen=True)
class FundingModelConfig:
    """Kalshi perp funding mechanics (per the kinetics-perps research doc).

    Kalshi funding is premium-driven with a DEAD-ZONE and a CLAMP, settled every 8h:
    if |premium| < zero_threshold the rate is set to 0; otherwise it is the premium
    clamped to ±clamp_cap. This differs from the crypto-CEX formula (an interest
    baseline plus clamp(interest − premium)); `interest` is kept for generality and
    defaults to 0 for Kalshi. All rates are per-interval FRACTIONS (0.0001 = 1 bp).
    """
    interval_hours: int = 8           # Kalshi BTCPERP/ETHPERP settle every 8h
    clamp_cap: float = 0.02           # ±2% / 8h hard clamp
    zero_threshold: float = 0.0001    # |premium| < 1 bp / 8h -> funding 0 (dead-zone)
    interest: float = 0.0             # CEX-style baseline; 0 for Kalshi

    @property
    def intervals_per_year(self) -> float:
        return 365.0 * 24.0 / self.interval_hours


@dataclass(frozen=True)
class FeeConfig:
    """Round-trip cost model. Kalshi perps are in a $0 LAUNCH PROMO (2026-06), so
    `promo` reproduces today's reality; `taker_bps`/`maker_bps` are a deliberately
    conservative post-promo PLACEHOLDER (Kalshi has not published the perp schedule
    — flagged in the research). The edge gate runs against the realistic fee so a
    result is never a promo artifact. bps are of notional, per executed side.
    """
    taker_bps: float = 2.0            # placeholder post-promo taker (per side, of notional)
    maker_bps: float = 0.0           # placeholder post-promo maker (per side)
    promo: bool = False              # True -> all fees 0 (today's Kalshi launch promo)

    def per_side(self, maker: bool = False) -> float:
        if self.promo:
            return 0.0
        return (self.maker_bps if maker else self.taker_bps) / 1e4


@dataclass(frozen=True)
class BacktestConfig:
    funding: FundingModelConfig = field(default_factory=FundingModelConfig)
    fees: FeeConfig = field(default_factory=FeeConfig)
    # position rule: take a side only when |forecast funding| clears the round-trip
    # fee by this safety multiple (so we don't trade into costs we can't beat).
    entry_fee_multiple: float = 1.0
    # AR(1) coefficient for the ar1 forecast (funding is ~0.97-0.99 autocorrelated).
    ar1_phi: float = 0.92
    bootstrap_n: int = 2000
    seed: int = 7
    warmup: int = 24                 # intervals used only to seed trailing forecasts
    # segments to report over (always per-segment, never one global number).
    segment_cols: tuple[str, ...] = ("regime", "basis_tier")


@dataclass(frozen=True)
class CollectorConfig:
    """Forward-capture poller settings (Phase C). 60s matches the 1-min-candle funding
    TWAP; depth=10 captures top-of-book L2 for later imbalance study. symbols=None ->
    poll every ACTIVE perp (you cannot backfill, so collect broad)."""
    interval_secs: int = 60
    depth: int = 10
    symbols: tuple[str, ...] | None = None   # None => all active perps
    settlement_refresh: bool = True          # upsert recent funding settlements when newly due
    settlement_lookback_days: int = 3
    funding_interval_hint: int = 8           # Kalshi perps settle 8h; only gates refresh cadence
    capture_trades: bool = True              # store the Kalshi taker tape each poll
    trade_limit: int = 100                   # recent prints to pull per symbol
    capture_crossvenue: bool = True          # also poll offshore funding (Binance/Bybit/Hyperliquid)
    jitter_secs: float = 2.0                 # small randomization so polls don't align to the second
