from kairos.config import BacktestConfig, FeeConfig
from kairos.fees import entry_threshold, per_side, position_change_cost, round_trip


def test_promo_zeroes_all_fees():
    f = FeeConfig(promo=True)
    assert per_side(f) == 0.0
    assert round_trip(f) == 0.0


def test_taker_bps_to_fraction():
    f = FeeConfig(taker_bps=5.0, maker_bps=1.0)
    assert abs(per_side(f) - 5e-4) < 1e-12
    assert abs(per_side(f, maker=True) - 1e-4) < 1e-12
    assert abs(round_trip(f) - 1e-3) < 1e-12


def test_position_change_cost_counts_sides():
    f = FeeConfig(taker_bps=2.0)
    assert position_change_cost(0, 0, f) == 0.0
    assert abs(position_change_cost(0, 1, f) - 2e-4) < 1e-12       # one side
    assert abs(position_change_cost(1, -1, f) - 2 * 2e-4) < 1e-12  # flip = two sides


def test_entry_threshold_scales_with_multiple():
    cfg = BacktestConfig(fees=FeeConfig(taker_bps=2.0), entry_fee_multiple=2.0)
    assert abs(entry_threshold(cfg) - round_trip(cfg.fees) * 2.0) < 1e-12
