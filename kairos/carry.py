"""Delta-neutral carry strategy + cross-market relative value.

The strategy is intentionally simple so the backtest measures the SIGNAL, not a
clever overlay: each interval, take the funding-receiving side iff the forecast
funding clears the fee threshold, hold delta-neutral (price legs cancel), collect
the REALIZED funding, and pay a fee only when the position changes.

Sign convention: signal s ∈ {−1, 0, +1} is the side that RECEIVES funding when the
forecast has that sign. s=+1 => predict positive funding => short the perp => receive
realized funding (which is +ve if funding stays positive, a loss if it flips). So
per-interval pnl = s · realized_funding − change_cost. This is the honest test: a
forecast only helps if acting on it beats both never-trading and always-collecting,
NET of cost.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import BacktestConfig
from .fees import entry_threshold, per_side


def _signal(forecast: float, threshold: float) -> int:
    if forecast > threshold:
        return 1
    if forecast < -threshold:
        return -1
    return 0


@dataclass(frozen=True)
class StrategyResult:
    pnl: np.ndarray          # per-interval net pnl (fraction of notional)
    signals: np.ndarray      # the {-1,0,+1} position each interval
    gross: float             # summed funding collected before fees
    net: float               # summed pnl after change costs
    n_trades: int            # number of position changes
    turnover: float          # mean |Δposition| per interval


def run_strategy(
    forecasts: np.ndarray,
    realized_funding: np.ndarray,
    cfg: BacktestConfig,
    maker: bool = False,
) -> StrategyResult:
    """Walk a funding path acting on `forecasts`; collect `realized_funding`, net of
    fees paid on position changes. The forecast and the realized series are aligned:
    forecasts[i] predicts realized_funding[i] (next interval at decision time i)."""
    fc = np.asarray(forecasts, float)
    rf = np.asarray(realized_funding, float)
    if fc.shape != rf.shape:
        raise ValueError("forecasts and realized_funding must align")
    thr = entry_threshold(cfg, maker=maker)
    side_cost = per_side(cfg.fees, maker=maker)
    pnl = np.empty_like(rf)
    sig = np.empty_like(rf, dtype=int)
    prev = 0
    trades = 0
    dsum = 0
    for i in range(len(rf)):
        s = _signal(float(fc[i]), thr)
        d = abs(s - prev)
        if d:
            trades += 1
        dsum += d
        pnl[i] = s * rf[i] - d * side_cost
        sig[i] = s
        prev = s
    return StrategyResult(
        pnl=pnl,
        signals=sig,
        gross=float(np.sum(sig * rf)),
        net=float(np.sum(pnl)),
        n_trades=trades,
        turnover=float(dsum / len(rf)) if len(rf) else 0.0,
    )


def funding_spread(funding_a: np.ndarray, funding_b: np.ndarray) -> np.ndarray:
    """Cross-market funding dispersion (the fatter, market-neutral RV source). Long
    the lower-funding leg, short the higher-funding leg, collect the spread."""
    return np.asarray(funding_a, float) - np.asarray(funding_b, float)
