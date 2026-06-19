"""Phase-A/B backtest: the KAIROS basis nowcast vs the no_change and raw_carry
baselines, scored on funding-forecast error AND net-of-fee carry pnl.

The cheap, decisive question (memo findings 1-3): does pushing the CURRENT basis
through the funding clamp predict next interval's funding better than persistence,
and does acting on it beat both never-trading-better-than-persistence and the naive
always-collect carry, NET of a realistic fee? We report per-segment (by regime and
basis width) with a paired bootstrap CI on the forecast-error delta. Selection is on
MAE / net pnl, never gross APR. A clean null is a successful outcome.

Source-agnostic: the same code scores synthetic rows (`synth`) or real Kalshi
funding history (`data.kalshi`) — both arrive in the canonical schema.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .basis import annualize
from .carry import run_strategy
from .config import BacktestConfig
from .metrics import (
    PnlSummary,
    directional_accuracy,
    mae,
    paired_abserr_delta_ci,
    pnl_summary,
)
from .model import add_forecasts


def _strategy_summary(df: pd.DataFrame, col: str, cfg: BacktestConfig) -> PnlSummary:
    res = run_strategy(df[col].to_numpy(float), df["funding_next"].to_numpy(float), cfg)
    return pnl_summary(res.pnl, res.signals, res.gross, res.n_trades, res.turnover, cfg.funding)


def _segment_row(name: str, value: str, g: pd.DataFrame, cfg: BacktestConfig) -> dict:
    y = g["funding_next"].to_numpy(float)
    nowcast = g["f_nowcast"].to_numpy(float)
    nochange = g["f_nochange"].to_numpy(float)
    delta, lo, hi = paired_abserr_delta_ci(y, nowcast, nochange, cfg.bootstrap_n, cfg.seed)
    sN = _strategy_summary(g, "f_nowcast", cfg)
    sC = _strategy_summary(g, "f_rawcarry", cfg)
    sP = _strategy_summary(g, "f_nochange", cfg)
    return {
        "segment": name,
        "value": value,
        "n": len(g),
        # forecast skill (bps, since funding fractions are tiny)
        "base_mae_bp": round(mae(y, nochange) * 1e4, 3),
        "nowcast_mae_bp": round(mae(y, nowcast) * 1e4, 3),
        "mae_delta_bp": round(delta * 1e4, 3),
        "mae_ci_bp": f"[{lo*1e4:+.3f},{hi*1e4:+.3f}]",
        "beats_base": bool(hi < 0),                  # nowcast genuinely sharper than persistence
        "nowcast_diracc": round(directional_accuracy(y, nowcast), 3),
        "base_diracc": round(directional_accuracy(y, nochange), 3),
        # net-of-fee strategy economics (annualized %)
        "nowcast_net_apr%": round(sN.annualized * 100, 2),
        "carry_net_apr%": round(sC.annualized * 100, 2),
        "persist_net_apr%": round(sP.annualized * 100, 2),
        "beats_carry": bool(sN.net > sC.net),
        "nowcast_hit": round(sN.hit_rate, 3) if sN.hit_rate == sN.hit_rate else float("nan"),
        "nowcast_turnover": round(sN.turnover, 3),
    }


def _report(df: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    rows = [_segment_row("ALL", "—", df, cfg)]
    for col in cfg.segment_cols:
        if col not in df.columns:
            continue
        for value, g in df.groupby(col):
            if len(g) >= 50:
                rows.append(_segment_row(col, str(value), g, cfg))
    return pd.DataFrame(rows)


def run_backtest(cfg: BacktestConfig, df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Score a preloaded canonical-schema frame; return (scored_with_forecasts, report).

    Drops the warmup prefix (used only to seed trailing forecasts) before scoring.
    """
    if len(df) <= cfg.warmup + 10:
        raise RuntimeError(f"Need > {cfg.warmup + 10} rows; got {len(df)}.")
    scored = add_forecasts(df.reset_index(drop=True), cfg)
    scored = scored.iloc[cfg.warmup :].reset_index(drop=True)
    return scored, _report(scored, cfg)


def format_report(report: pd.DataFrame) -> str:
    with pd.option_context("display.max_rows", None, "display.width", 240):
        return report.to_string(index=False)


def headline(scored: pd.DataFrame, cfg: BacktestConfig) -> str:
    """One-line economic read on the full sample."""
    y = scored["funding_next"].to_numpy(float)
    base = annualize(float(np.mean(np.abs(y - scored["f_nochange"].to_numpy(float)))), cfg.funding)
    now = annualize(float(np.mean(np.abs(y - scored["f_nowcast"].to_numpy(float)))), cfg.funding)
    return f"full-sample funding MAE (annualized bps-equiv): no_change={base*1e4:.2f}  nowcast={now*1e4:.2f}"
