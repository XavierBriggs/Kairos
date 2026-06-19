from kairos.basis import funding_from_basis_bps
from kairos.config import FundingModelConfig
from kairos.funding import ar1, basis_nowcast, no_change, raw_carry

CFG = FundingModelConfig()


def test_no_change_is_persistence():
    assert no_change(0.0007) == 0.0007


def test_raw_carry_returns_trailing_mean():
    assert raw_carry(0.0003) == 0.0003


def test_ar1_endpoints():
    now, mean = 0.0010, 0.0002
    assert abs(ar1(now, mean, 1.0) - now) < 1e-12        # phi=1 -> persistence
    assert abs(ar1(now, mean, 0.0) - mean) < 1e-12       # phi=0 -> mean
    # phi in between sits strictly between
    mid = ar1(now, mean, 0.5)
    assert mean < mid < now


def test_basis_nowcast_matches_clamp_map():
    for b in (3.0, 25.0, -40.0):
        assert basis_nowcast(b, CFG) == funding_from_basis_bps(b, CFG)
