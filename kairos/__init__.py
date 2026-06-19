"""KAIROS — perp funding-rate / basis relative-value research harness for FORTUNA.

Kairos (καιρός) is the *opportune moment to act* — which is exactly what a funding /
basis strategy hunts: the right instant to put on the carry / RV trade. The core
object is the basis, `mark − reference`, where the reference is a PLUGGABLE interface
— a tradeable spot index for crypto perps, a belief-model probability for FORTUNA's
event perps. Swapping the reference is the entire port. See README.md and
docs/research/2026-06-18-perpetual-futures-modeling.md.
"""
