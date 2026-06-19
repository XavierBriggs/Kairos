import numpy as np

from kairos.config import FundingModelConfig
from kairos.metrics import (
    bootstrap_mean_ci,
    directional_accuracy,
    mae,
    paired_abserr_delta_ci,
    pnl_summary,
)

CFG = FundingModelConfig()


def test_mae_zero_when_exact():
    y = np.array([0.1, -0.2, 0.3])
    assert mae(y, y) == 0.0


def test_directional_accuracy():
    y = np.array([1.0, -1.0, 1.0, -1.0])
    yhat = np.array([2.0, -3.0, -0.5, -0.1])  # 3/4 signs match
    assert abs(directional_accuracy(y, yhat) - 0.75) < 1e-12


def test_paired_delta_ci_detects_a_better_model():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 1, 500)
    p_model = y + rng.normal(0, 0.1, 500)   # near-perfect
    p_base = y + rng.normal(0, 1.0, 500)    # noisy
    point, lo, hi = paired_abserr_delta_ci(y, p_model, p_base, n_boot=500, seed=1)
    assert point < 0      # model has lower error
    assert hi < 0         # CI fully below zero => genuinely better


def test_pnl_summary_fields():
    pnl = np.array([0.001, -0.0005, 0.001, 0.0])
    sig = np.array([1, 1, 1, 0])
    s = pnl_summary(pnl, sig, gross=0.0015, n_trades=1, turnover=0.25, cfg=CFG)
    assert s.n == 4
    assert abs(s.net - pnl.sum()) < 1e-12
    assert abs(s.annualized - pnl.mean() * CFG.intervals_per_year) < 1e-12
    # hit rate over TRADED intervals only (3 traded: 2 positive)
    assert abs(s.hit_rate - 2 / 3) < 1e-9


def test_bootstrap_mean_ci_brackets_point():
    x = np.array([0.001] * 100)
    point, lo, hi = bootstrap_mean_ci(x, n_boot=200, seed=2)
    assert abs(point - 0.001) < 1e-9
    assert lo <= point <= hi
