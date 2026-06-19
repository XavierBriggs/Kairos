"""Attach the competing forecasts to a canonical-schema frame.

Given rows with `funding_now`, `basis_bps` (as-of priors) and the `funding_next`
label, produce four forecast columns aligned to predict `funding_next`. Trailing
state uses only past+current observations (expanding mean) — leakage-safe, the
forecast at row i never sees row i's label.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import BacktestConfig
from .funding import basis_nowcast_series


def _expanding_mean(x: np.ndarray) -> np.ndarray:
    """Mean of x[0..i] inclusive (as-of i; current funding is known at decision i)."""
    c = np.cumsum(x)
    return c / (np.arange(len(x)) + 1.0)


def add_forecasts(df: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """Return a copy with f_nochange / f_rawcarry / f_ar1 / f_nowcast columns."""
    fn = df["funding_now"].to_numpy(float)
    bb = df["basis_bps"].to_numpy(float)
    trailing = _expanding_mean(fn)
    out = df.copy()
    out["f_nochange"] = fn
    out["f_rawcarry"] = trailing
    out["f_ar1"] = trailing + cfg.ar1_phi * (fn - trailing)
    out["f_nowcast"] = basis_nowcast_series(bb, cfg.funding)
    return out


# The forecast columns the backtest scores, mapped to display names.
FORECASTS: dict[str, str] = {
    "f_nochange": "no_change",
    "f_rawcarry": "raw_carry",
    "f_ar1": "ar1",
    "f_nowcast": "nowcast",
}
