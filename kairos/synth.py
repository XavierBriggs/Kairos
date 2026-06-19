"""Deterministic synthetic funding/basis series — the zero-data demo.

The point is NOT to claim real edge; it is to prove the wiring and to show the
harness can DETECT a basis nowcast beating persistence WHEN the data-generating
truth makes the current basis a fresher read of the premium than the last settled
funding — and to show a near-NULL in the calm regime where it doesn't.

The truth is a fine-grained premium path with regime switching (calm: tiny premium
inside the dead-zone, very persistent; stressed: large, swinging, sign-flipping).
Funding settled at the end of an interval is the CLAMP of the premium's MEAN over
that interval (a lagged average); the observed basis is the premium at the END of
the interval (the latest read). Because the premium trends, the latest basis leads
next interval's mean-premium better than the just-settled funding does — exactly
the thesis. On real Kalshi data (`data.kalshi`) the same schema is produced from
funding history; the backtest is source-agnostic.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .basis import basis_bps as _basis_bps
from .basis import funding_from_premium
from .config import FundingModelConfig

_REF_PRICE = 60_000.0  # synthetic spot/index level (BTC-ish); only ratios matter


def _basis_tier(b_bps: float) -> str:
    a = abs(b_bps)
    if a >= 50:
        return "wide>=50bp"
    if a >= 10:
        return "mid10-50bp"
    return "tight<10bp"


def make_series(
    n: int = 3000,
    seed: int = 7,
    sub_steps: int = 8,
    symbol: str = "KXBTCPERP",
) -> pd.DataFrame:
    """Generate `n` interval rows in the canonical, leakage-safe schema."""
    rng = np.random.default_rng(seed)
    cfg = FundingModelConfig()
    total_sub = (n + 2) * sub_steps  # +2 so the last row has a next-interval window
    # --- regime-switching latent premium path at sub-interval resolution ---
    p = np.empty(total_sub)
    p[0] = 0.0
    stressed = False
    for k in range(1, total_sub):
        # Markov regime switch (persistent regimes)
        if stressed:
            stressed = rng.random() > 0.06   # ~stay stressed
        else:
            stressed = rng.random() > 0.97   # ~rarely enter stress
        if stressed:
            phi, vol, target = 0.85, 0.00060, 0.0
            # occasional sign-flipping shocks (crowded longs -> squeeze, etc.)
            shock = rng.normal(0.0, vol) + (rng.normal(0, 0.0015) if rng.random() < 0.05 else 0.0)
        else:
            phi, vol, target = 0.985, 0.00004, 0.00004  # tiny, mostly inside dead-zone
            shock = rng.normal(0.0, vol)
        p[k] = target + phi * (p[k - 1] - target) + shock

    rows = []
    base_ts = pd.Timestamp("2026-01-01T00:00:00Z")
    for i in range(n):
        a, b, c = i * sub_steps, (i + 1) * sub_steps, (i + 2) * sub_steps
        prem_now_mean = float(p[a:b].mean())          # premium avg over THIS interval
        prem_next_mean = float(p[b:c].mean())         # premium avg over NEXT interval
        prem_latest = float(p[b - 1])                 # latest premium = the observed basis
        regime = "stressed" if abs(p[b - 1]) > 0.0003 else "calm"

        funding_now = funding_from_premium(prem_now_mean, cfg)
        funding_next = funding_from_premium(prem_next_mean, cfg) + float(rng.normal(0, 1e-6))
        reference = _REF_PRICE
        mark = reference * (1.0 + prem_latest)        # basis carries the latest premium
        b_bps = _basis_bps(mark, reference)

        rows.append(
            {
                "ts": base_ts + pd.Timedelta(hours=cfg.interval_hours * i),
                "venue": "synthetic",
                "symbol": symbol,
                "interval_hours": cfg.interval_hours,
                "funding_now": funding_now,
                "mark": mark,
                "reference": reference,
                "basis_bps": b_bps,
                "funding_next": funding_next,
                "regime": regime,
                "basis_tier": _basis_tier(b_bps),
            }
        )
    return pd.DataFrame(rows)
