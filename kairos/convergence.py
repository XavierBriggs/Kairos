"""Phase-C forward edge — the CLV-equivalent that actually proves (or kills) edge.

Phase A/B tell you the nowcast is calibrated and whether there is carry to harvest.
They do NOT prove that acting on a LIVE, independent basis (mark vs reference,
captured forward — not reconstructed from funding) earns positive net carry out of
sample. That is this file: over a forward window of captured rows, run the nowcast
strategy, and report the net-of-fee edge with a CI, plus how often realized funding
converged toward the nowcast rather than toward persistence. Positive, persistent
forward edge before any capital is FORTUNA's I7 forward-validation discipline; a
clean null is a successful, money-saving outcome.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .carry import run_strategy
from .config import BacktestConfig
from .metrics import bootstrap_mean_ci
from .model import add_forecasts


@dataclass(frozen=True)
class ForwardEdge:
    n: int
    nowcast_net_apr: float       # annualized net-of-fee carry from trading the nowcast
    net_ci_apr: tuple[float, float]
    edge_positive: bool          # CI lower bound > 0 => distinguishable from zero
    sign_agreement: float        # frac where sign(nowcast) == sign(realized funding)
    convergence_ratio: float     # frac where realized funding is closer to nowcast than to persistence
    carry_net_apr: float         # the raw-carry baseline over the same window


def forward_edge(df: pd.DataFrame, cfg: BacktestConfig) -> ForwardEdge:
    """Score a forward-captured canonical-schema frame (with a TRUE independent
    basis from live mark/reference, not reconstructed from funding)."""
    scored = add_forecasts(df.reset_index(drop=True), cfg)
    y = scored["funding_next"].to_numpy(float)
    nowcast = scored["f_nowcast"].to_numpy(float)
    nochange = scored["f_nochange"].to_numpy(float)

    res = run_strategy(nowcast, y, cfg)
    point, lo, hi = bootstrap_mean_ci(res.pnl, cfg.bootstrap_n, cfg.seed)
    ipy = cfg.funding.intervals_per_year

    carry = run_strategy(scored["f_rawcarry"].to_numpy(float), y, cfg)

    closer = np.abs(y - nowcast) < np.abs(y - nochange)
    return ForwardEdge(
        n=len(scored),
        nowcast_net_apr=point * ipy,
        net_ci_apr=(lo * ipy, hi * ipy),
        edge_positive=bool(lo > 0),
        sign_agreement=float(np.mean(np.sign(nowcast) == np.sign(y))),
        convergence_ratio=float(np.mean(closer)),
        carry_net_apr=float(carry.net / max(len(y), 1) * ipy),
    )
