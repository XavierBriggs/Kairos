"""Basis and the funding clamp — the mechanical core.

Funding is not an independent quantity: it is a clamped, dead-zoned transform of
the PREMIUM, and the premium is just the fractional basis `(mark − reference)`.
That identity is the whole reason to nowcast basis rather than forecast funding
directly. All rates here are per-interval FRACTIONS (0.0001 = 1 bp = 0.01%).
"""
from __future__ import annotations

import numpy as np

from .config import FundingModelConfig


def basis_bps(mark: float, reference: float) -> float:
    """Signed basis in bps: positive => perp rich to reference => longs pay shorts."""
    if reference == 0:
        raise ValueError("reference is zero; cannot form a basis")
    return (mark - reference) / reference * 1e4


def premium_from_basis_bps(b_bps: float) -> float:
    """The premium index IS the fractional basis. bps -> fraction."""
    return b_bps / 1e4


def funding_from_premium(premium: float, cfg: FundingModelConfig) -> float:
    """Kalshi-shaped funding: dead-zone then clamp.

    |premium| < zero_threshold -> 0 (noise dead-zone); else premium (+ interest,
    0 for Kalshi) clamped to ±clamp_cap. This is the map a basis nowcast pushes the
    current basis through to predict next interval's funding.
    """
    eff = 0.0 if abs(premium) < cfg.zero_threshold else premium
    eff += cfg.interest
    return float(np.clip(eff, -cfg.clamp_cap, cfg.clamp_cap))


def funding_from_basis_bps(b_bps: float, cfg: FundingModelConfig) -> float:
    """Convenience: basis (bps) -> predicted funding (the basis nowcast map)."""
    return funding_from_premium(premium_from_basis_bps(b_bps), cfg)


def is_saturated(funding: float, cfg: FundingModelConfig) -> bool:
    """True if funding sits on the clamp — the true premium is larger in magnitude
    than the rate reveals (so de-clamping only yields a lower bound)."""
    return abs(abs(funding) - cfg.clamp_cap) < 1e-12


def implied_premium_from_funding(funding: float, cfg: FundingModelConfig) -> float:
    """Recover a point estimate of the premium/basis from a realized funding.

    Used to reconstruct historical basis when only funding+mark are stored (Kalshi
    funding history has no index). Exact off the clamp/dead-zone; a bound on it.
    """
    return funding - cfg.interest


def implied_basis_bps_from_funding(funding: float, cfg: FundingModelConfig) -> float:
    return implied_premium_from_funding(funding, cfg) * 1e4


def annualize(per_interval_rate: float, cfg: FundingModelConfig) -> float:
    """Simple (non-compounded) annualization of a per-interval funding/return."""
    return per_interval_rate * cfg.intervals_per_year


def basis_tier(b_bps: float) -> str:
    """Coarse width bucket for per-segment reporting (shared by synth + live data)."""
    a = abs(b_bps)
    if a >= 50:
        return "wide>=50bp"
    if a >= 10:
        return "mid10-50bp"
    return "tight<10bp"
