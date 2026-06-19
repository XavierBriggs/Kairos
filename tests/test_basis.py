import numpy as np

from kairos.basis import (
    annualize,
    basis_bps,
    funding_from_basis_bps,
    funding_from_premium,
    implied_basis_bps_from_funding,
    is_saturated,
)
from kairos.config import FundingModelConfig

CFG = FundingModelConfig()


def test_basis_sign_and_magnitude():
    assert basis_bps(60_030.0, 60_000.0) > 0          # perp rich -> positive basis
    assert abs(basis_bps(60_030.0, 60_000.0) - 5.0) < 1e-6  # 30/60000 = 5 bp
    assert basis_bps(59_970.0, 60_000.0) < 0


def test_dead_zone_zeros_small_premium():
    tiny = CFG.zero_threshold * 0.5
    assert funding_from_premium(tiny, CFG) == 0.0
    assert funding_from_premium(-tiny, CFG) == 0.0


def test_clamp_caps_extreme_premium():
    assert funding_from_premium(0.5, CFG) == CFG.clamp_cap
    assert funding_from_premium(-0.5, CFG) == -CFG.clamp_cap
    assert is_saturated(funding_from_premium(0.5, CFG), CFG)


def test_midrange_premium_passes_through():
    p = 0.001  # 10 bp, outside dead-zone, inside clamp
    assert abs(funding_from_premium(p, CFG) - p) < 1e-12


def test_declamp_inverts_in_midrange():
    # outside the dead-zone and clamp, basis -> funding -> implied basis round-trips
    for b in (5.0, 12.0, -8.0, 30.0, -45.0):
        f = funding_from_basis_bps(b, CFG)
        assert abs(implied_basis_bps_from_funding(f, CFG) - b) < 1e-9


def test_annualize_scales_by_intervals_per_year():
    assert abs(annualize(0.0001, CFG) - 0.0001 * (365 * 24 / 8)) < 1e-12
    assert abs(CFG.intervals_per_year - 1095.0) < 1e-9


def test_funding_from_basis_series_consistent():
    bs = np.array([0.5, 20.0, -20.0])
    fs = [funding_from_basis_bps(float(b), CFG) for b in bs]
    assert fs[0] == 0.0  # 0.5 bp basis -> premium 0.5e-4 < zero_threshold -> dead-zone
    assert fs[1] > 0 and fs[2] < 0
