"""Scoring — forecast skill and net-of-cost strategy quality.

Two things get scored, never ROI on its own (ROI overfits; see the memo):
(1) forecast accuracy of next-interval funding — MAE / RMSE / directional accuracy,
plus a paired bootstrap CI on the model-minus-baseline absolute-error delta (the
Phase-A/B gate); (2) the strategy's NET-of-fee pnl summary. Selection runs on these.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .basis import annualize
from .config import FundingModelConfig


def mae(y: np.ndarray, yhat: np.ndarray) -> float:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return float(np.mean(np.abs(y - yhat)))


def rmse(y: np.ndarray, yhat: np.ndarray) -> float:
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return float(np.sqrt(np.mean((y - yhat) ** 2)))


def directional_accuracy(y: np.ndarray, yhat: np.ndarray) -> float:
    """Fraction of intervals where the forecast got the SIGN of funding right.
    Sign-0 (dead-zone) on either side counts as a match only if both are 0."""
    y, yhat = np.asarray(y, float), np.asarray(yhat, float)
    return float(np.mean(np.sign(y) == np.sign(yhat)))


def paired_abserr_delta_ci(
    y: np.ndarray,
    p_model: np.ndarray,
    p_base: np.ndarray,
    n_boot: int = 2000,
    seed: int = 7,
    alpha: float = 0.05,
) -> tuple[float, float, float]:
    """CI for mean(|y−model| − |y−base|) on the SAME intervals.

    Negative with the CI fully below 0 => the model has genuinely lower funding-
    forecast error than the baseline in that segment. This is KAIROS's Phase-A/B
    gate (the analogue of HEATER's paired log-loss delta)."""
    y = np.asarray(y, float)
    em = np.abs(y - np.asarray(p_model, float))
    eb = np.abs(y - np.asarray(p_base, float))
    diff = em - eb
    n = len(diff)
    point = float(diff.mean()) if n else float("nan")
    if n == 0:
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    boots = np.array([diff[rng.integers(0, n, n)].mean() for _ in range(n_boot)])
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return point, float(lo), float(hi)


@dataclass(frozen=True)
class PnlSummary:
    n: int
    net: float              # summed net pnl (fraction of notional)
    gross: float
    mean_per_interval: float
    annualized: float       # simple annualization of mean-per-interval
    hit_rate: float         # fraction of traded intervals with positive net pnl
    n_trades: int
    turnover: float


def pnl_summary(
    pnl: np.ndarray,
    signals: np.ndarray,
    gross: float,
    n_trades: int,
    turnover: float,
    cfg: FundingModelConfig,
) -> PnlSummary:
    pnl = np.asarray(pnl, float)
    sig = np.asarray(signals, float)
    traded = sig != 0
    n_traded = int(traded.sum())
    hit = float(np.mean(pnl[traded] > 0)) if n_traded else float("nan")
    mean_pi = float(pnl.mean()) if len(pnl) else float("nan")
    return PnlSummary(
        n=len(pnl),
        net=float(pnl.sum()),
        gross=float(gross),
        mean_per_interval=mean_pi,
        annualized=annualize(mean_pi, cfg),
        hit_rate=hit,
        n_trades=n_trades,
        turnover=turnover,
    )


def bootstrap_mean_ci(x: np.ndarray, n_boot: int = 2000, seed: int = 7, alpha: float = 0.05):
    """(point, lo, hi) for the mean of x via deterministic resampling. Used to put a
    CI on per-interval net pnl (is the strategy's edge distinguishable from 0?)."""
    x = np.asarray(x, float)
    n = len(x)
    point = float(x.mean()) if n else float("nan")
    if n == 0:
        return point, float("nan"), float("nan")
    rng = np.random.default_rng(seed)
    boots = np.array([x[rng.integers(0, n, n)].mean() for _ in range(n_boot)])
    lo, hi = np.quantile(boots, [alpha / 2, 1 - alpha / 2])
    return point, float(lo), float(hi)
