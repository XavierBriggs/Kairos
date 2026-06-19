"""The pluggable reference — what a perpetual is tethered to by funding.

This is the seam that keeps the harness uncoupled from crypto and portable to
FORTUNA's event perps. The basis the whole model is built on is `mark − reference`;
everything downstream (basis -> funding clamp, the forecasts, the carry strategy,
the validation) is reference-AGNOSTIC. Only this file changes between worlds:

  - SpotIndexReference  (crypto perp): reference = the spot/index price. A tradeable
    spot exists, so funding is pinned to basis by cash-and-carry arbitrage —
    convergence is HARD. This is what we validate on Kalshi crypto perps today.

  - BeliefReference     (event perp):  reference = a model's probability in [0,1].
    There is NO tradeable spot to carry against, so funding is a demand-balancing
    fee, not an arbitrage-pinned basis — convergence is SOFT, the target is bounded,
    and it jumps to 0/1 at resolution. The valuation/risk layer must be rebuilt
    (see docs/research/2026-06-18-perpetual-futures-modeling.md, finding 5). This
    stub fixes the INTERFACE so the port is a swap, not a rewrite.

`convergence_strength` ∈ [0,1] makes the hard/soft distinction a parameter the
backtest can reason about rather than an assumption baked into the core.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class Reference(Protocol):
    """A reference value the perp mark is compared against."""

    name: str

    def value(self, ctx: dict) -> float:
        """Return the reference level for one observation (a price, or a prob in [0,1])."""
        ...

    @property
    def bounded(self) -> bool:
        """True if the reference lives in [0,1] (event probability) vs an unbounded price."""
        ...

    @property
    def convergence_strength(self) -> float:
        """How hard funding pins mark->reference: ~1 when a tradeable spot enforces
        cash-and-carry (crypto), <1 when convergence is only a demand-balancing fee
        (events)."""
        ...


@dataclass(frozen=True)
class SpotIndexReference:
    """Crypto perp: reference = the spot/index price (Kalshi's `reference_price`).

    A tradeable spot exists -> cash-and-carry arbitrage pins funding to basis ->
    convergence is hard. `value` reads the index from the observation context.
    """

    name: str = "spot_index"
    index_key: str = "reference"

    def value(self, ctx: dict) -> float:
        return float(ctx[self.index_key])

    @property
    def bounded(self) -> bool:
        return False

    @property
    def convergence_strength(self) -> float:
        return 1.0


@dataclass(frozen=True)
class BeliefReference:
    """Event perp: reference = a belief-model probability in [0,1] (e.g. Aeolus).

    No tradeable spot -> funding is a demand-balancing fee, convergence is soft, the
    target is bounded and jumps to {0,1} at resolution. This stub fixes the interface
    for FORTUNA's belief->edge pipeline; the bounded/jump-aware valuation & margin
    layers are explicitly OUT OF SCOPE here (rebuild work, not a port).
    """

    name: str = "belief"
    prob_key: str = "belief_prob"
    convergence: float = 0.5  # soft: tune per market; NOT 1.0

    def value(self, ctx: dict) -> float:
        p = float(ctx[self.prob_key])
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"belief probability out of [0,1]: {p}")
        return p

    @property
    def bounded(self) -> bool:
        return True

    @property
    def convergence_strength(self) -> float:
        return self.convergence
