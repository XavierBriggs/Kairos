import numpy as np

from kairos.carry import funding_spread, run_strategy
from kairos.config import BacktestConfig, FeeConfig, FundingModelConfig

# Fees small enough that a clear funding signal clears the threshold.
CFG = BacktestConfig(fees=FeeConfig(taker_bps=1.0), funding=FundingModelConfig())


def test_constant_positive_funding_collects_minus_one_entry():
    fc = np.full(10, 0.001)   # forecast well above threshold
    rf = np.full(10, 0.001)   # realized matches
    res = run_strategy(fc, rf, CFG)
    assert np.all(res.signals == 1)             # always short to receive +funding
    assert res.n_trades == 1                    # one entry, then held
    assert res.gross > 0
    # net = gross minus a single entry side-cost
    side = CFG.fees.per_side()
    assert abs(res.net - (res.gross - side)) < 1e-12


def test_below_threshold_does_not_trade():
    tiny = CFG.fees.per_side() * 0.1            # forecast below round-trip threshold
    fc = np.full(10, tiny)
    rf = np.full(10, 0.001)
    res = run_strategy(fc, rf, CFG)
    assert np.all(res.signals == 0)
    assert res.net == 0.0
    assert res.n_trades == 0


def test_wrong_direction_loses():
    fc = np.full(10, 0.001)    # predicts positive -> short
    rf = np.full(10, -0.001)   # funding actually negative -> short PAYS
    res = run_strategy(fc, rf, CFG)
    assert res.gross < 0


def test_flip_costs_two_sides():
    fc = np.array([0.001, -0.001])
    rf = np.array([0.001, -0.001])
    res = run_strategy(fc, rf, CFG)
    assert res.signals.tolist() == [1, -1]
    assert res.n_trades == 2                    # enter, then flip


def test_funding_spread_is_difference():
    a = np.array([0.001, 0.002])
    b = np.array([0.0005, 0.0005])
    assert np.allclose(funding_spread(a, b), [0.0005, 0.0015])
