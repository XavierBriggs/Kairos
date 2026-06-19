"""The competing forecasts of NEXT interval's funding.

Three contenders, deliberately chosen so the gap between them isolates exactly the
theses worth money (memo findings 1-3):

  - no_change   : persistence. Funding's AR(1)≈0.97-0.99 makes this near-optimal and
                  brutally hard to beat — it is THE baseline to clear.
  - raw_carry   : "funding ≈ its trailing average". The naive carry-harvester who
                  always takes the structurally-favored side. The strategy baseline.
  - ar1         : mean-reversion toward the trailing mean at coefficient phi.
  - basis_nowcast (KAIROS): push the CURRENT basis through the funding clamp. A
                  fresher read of where the premium is than the last settled funding.

Pure functions; the backtest supplies trailing state (no peeking — see synth/data).
"""
from __future__ import annotations

import numpy as np

from .basis import funding_from_basis_bps
from .config import FundingModelConfig


def no_change(funding_now: float) -> float:
    """Persistence: next funding = this funding."""
    return funding_now


def raw_carry(trailing_mean: float) -> float:
    """Naive carry: next funding = the trailing-mean funding (the structural level)."""
    return trailing_mean


def ar1(funding_now: float, trailing_mean: float, phi: float) -> float:
    """Mean-reverting AR(1): mean + phi*(now − mean)."""
    return trailing_mean + phi * (funding_now - trailing_mean)


def basis_nowcast(basis_bps_now: float, cfg: FundingModelConfig) -> float:
    """KAIROS: map the current basis through the funding clamp to predict funding."""
    return funding_from_basis_bps(basis_bps_now, cfg)


def basis_nowcast_series(basis_bps: np.ndarray, cfg: FundingModelConfig) -> np.ndarray:
    return np.array([funding_from_basis_bps(float(b), cfg) for b in np.asarray(basis_bps, float)])
