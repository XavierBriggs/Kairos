"""Cost model — the silent edge-killer (memo: net ≪ gross APR).

Funding carry is collected by holding a delta-neutral position; you pay fees only
when the position CHANGES (open / flip / close), not every interval. So the right
unit is the cost of a position change, and the right trade filter is "only take a
side when the forecast funding clears the round-trip cost". All costs are fractions
of notional, matching the per-interval funding fractions.
"""
from __future__ import annotations

from .config import BacktestConfig, FeeConfig


def per_side(fees: FeeConfig, maker: bool = False) -> float:
    """Fraction-of-notional cost for one executed side."""
    return fees.per_side(maker=maker)


def round_trip(fees: FeeConfig, maker: bool = False) -> float:
    """Open + close cost."""
    return 2.0 * per_side(fees, maker=maker)


def position_change_cost(prev_pos: int, new_pos: int, fees: FeeConfig, maker: bool = False) -> float:
    """Cost of moving from prev_pos to new_pos in {-1,0,+1}. |Δ| sides executed."""
    return abs(new_pos - prev_pos) * per_side(fees, maker=maker)


def entry_threshold(cfg: BacktestConfig, maker: bool = False) -> float:
    """Minimum |forecast funding| worth a one-interval round trip — don't trade into
    costs you can't beat. Scaled by cfg.entry_fee_multiple as a safety margin."""
    return round_trip(cfg.fees, maker=maker) * cfg.entry_fee_multiple
