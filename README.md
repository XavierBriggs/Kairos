# KAIROS — perp funding / basis relative-value research harness

> *Kairos* (καιρός) — the opportune moment to act. KAIROS hunts the right time to put
> on a perp carry / basis trade. Its core object is the basis `mark − reference`, where
> the reference is a **pluggable interface** — a spot index for crypto perps, a belief
> probability for FORTUNA's event perps — so the same engine ports by swapping the
> reference, not rewriting the model.

KAIROS v0 is a research MVP that answers one cheap, decisive question:

> **Is there edge beyond persistence?** — does pushing the *current basis* through the
> funding clamp forecast next funding more sharply than no-change, and does acting on
> it beat the naive always-collect carry **net of a realistic fee**, on live data?

The research memo (`docs/research/2026-06-18-perpetual-futures-modeling.md`) is blunt:
funding is mechanically a clamped transform of the basis, its AR(1)≈0.99 makes
no-change near-optimal, and the real edge is **carry capture + cross-market RV, net of
cost** — a *decaying risk premium*, not a free lunch. So KAIROS does not chase a clever
funding model; it builds the basis nowcast, races it against the baselines that actually
matter, and wires the live read-only Kalshi feed that proves (or kills) edge forward.

This is **research code** (Python, exploratory). It is intentionally *not* in the Rust
workspace and *not* on any money path — FORTUNA's house rules (integer cents, no-unwrap,
`Clock` injection) govern the trading core, not this harness. The Kalshi client is
**read-only by construction** (no order/cancel methods exist in it). The eventual
production path is a Rust port of the validated model.

## The model in one breath

```
basis_bps   = (mark − reference)/reference · 1e4              # reference is PLUGGABLE (crypto vs event)
funding_hat = clamp(premium, ±2%) , 0 inside a 1bp dead-zone  # Kalshi funding mechanics
forecasts:  no_change (persistence) | raw_carry (collect the mean) | ar1 | basis_nowcast (KAIROS)
strategy:   take the funding-receiving side iff |funding_hat| clears the round-trip fee; hold
            delta-neutral; collect realized funding; pay a fee only when the position changes
edge proof: does basis_nowcast beat BOTH baselines on forecast MAE AND net-of-fee carry APR?
```

A **clean null** (nothing beats no-change / raw-carry after a realistic fee) is a
successful, money-saving outcome — exactly as in HEATER/DEUCE.

## The pluggable reference (the seam that fits FORTUNA)

`kairos/reference.py` is the only file that changes between crypto and event perps:

- **`SpotIndexReference`** (crypto) — reference = the spot/index price. A tradeable spot
  exists, so cash-and-carry arbitrage pins funding to basis → convergence is **hard**.
  This is what we validate on Kalshi crypto perps today.
- **`BeliefReference`** (event perp) — reference = a model probability in [0,1] (e.g.
  Aeolus). No spot to carry → funding is a demand-balancing fee, convergence is **soft**,
  the target is bounded and jumps to 0/1 at resolution. The interface is fixed so the
  port is a swap; the bounded/jump-aware valuation & margin layers are explicitly the
  *rebuild* work (memo finding 5), out of scope for v0.

## The three phases (Phase A built; B reachable live now; C forward)

| Phase | Data | Test | Status |
|-------|------|------|--------|
| **A. Calibration vs baselines** | synthetic regime-switching | nowcast beats no_change AND raw_carry on MAE / net APR | **built** (runs with no keys) |
| **B. Real Kalshi funding** | `GET /margin/funding_rates/historical` (read-only) | funding autocorrelation + carry economics on actual BTC/ETH PERP, net of realistic fee | **live now** |
| **C. Forward convergence** | poll mark + reference each interval | persistent forward net edge; the *independent-basis* nowcast | harness built (`convergence.py`) |

**Honest limit on Phase B:** Kalshi funding history stores funding + mark but **not the
index**, so basis is reconstructed by de-clamping funding and the nowcast **degenerates
to no_change on pure history by construction**. Phase B therefore validates persistence
+ carry economics; the *independent-basis* nowcast is only testable forward (Phase C),
by polling `mark` and `reference_price` live and scoring with `convergence.forward_edge`.

## Layout
```
kairos/
  config.py       # paths; Kalshi FundingModelConfig (±2%/8h clamp, 1bp dead-zone); FeeConfig (promo + realistic); loads KALSHI_* from fortuna/.env
  reference.py    # THE SEAM: Reference protocol + SpotIndexReference (crypto) + BeliefReference (event stub)
  basis.py        # basis_bps; funding clamp/dead-zone; de-clamp; annualize   (pure, tested)
  funding.py      # the four competing forecasts                              (pure, tested)
  fees.py         # round-trip cost model + trade threshold                   (pure, tested)
  carry.py        # delta-neutral carry strategy PnL; cross-market spread      (pure, tested)
  metrics.py      # MAE/dir-acc; net-PnL summary; paired bootstrap CI          (pure, tested)
  model.py        # attach forecasts to a canonical frame (leakage-safe trailing state)
  synth.py        # deterministic regime-switching synthetic series (zero-data demo)
  backtest.py     # Phase-A/B walk-forward, per-regime, net of fees, bootstrap CI
  convergence.py  # Phase-C forward edge / CLV-equivalent
  cli.py          # backtest / markets / funding
  data/kalshi.py  # READ-ONLY Kalshi perps client (RSA-PSS signing). NO order methods.
tests/            # basis, funding, fees, carry, metrics, backtest, kalshi(signing+parse)
scripts/get_funding.py   # pull real funding history -> data/kairos/<symbol>.csv
```

## Quickstart
```bash
cd docs/kairos
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest                                   # pure modules + signing/parse (no network)

# Phase-A backtest — runs out of the box on synthetic data (no keys, no network)
kairos backtest                          # add --n 5000 --seed 3 to vary; --promo for $0 fees

# live READ-ONLY Kalshi (creds from fortuna/.env): confirm signing + list perps
kairos markets

# Phase-B: pull real funding history and backtest on it (read-only)
kairos funding --symbol KXBTCPERP --days 30
kairos backtest --real --symbol KXBTCPERP --days 60

# Phase-C: forward capture into SQLite (read-only), then score the forward edge
kairos poll                              # one capture round -> data/kairos/kairos.db (cron unit)
kairos collect --interval 60             # the long-running loop (nohup/systemd unit)
kairos db-status                         # coverage: snapshots + settlements per symbol
kairos forward --symbol KXBTCPERP        # build labeled intervals -> ForwardEdge (I7 read)
```

## Phase-C forward capture (the proper DB)

The basis nowcast is **untestable on Kalshi history** (no stored index → basis collapses to
de-clamped funding → nowcast == no_change). The fix is forward capture into a SQLite store
(`data/kairos/kairos.db`, stdlib `sqlite3`, WAL):

- **`snapshot`** — every poll, per perp: mark / reference / settlement+liquidation mark /
  top-of-book + top-10 L2 / OI / volume / funding-estimate, plus derived `basis_bps`,
  `spread_bps`, `microprice`, `imbalance_l1`, plus full raw JSON (never lose a field).
- **`funding_settlement`** — the official 8h rates (04:00/12:00/20:00 UTC), upserted only when
  a new one is due (so the funding endpoint is hit ~once/symbol/8h, not every poll).
- **`poll_run`** — collector health/audit.

`kairos forward` joins them on demand into the leakage-safe canonical schema with a **true
independent basis** (the as-of snapshot strictly before each settlement) and runs
`convergence.forward_edge`. **Idempotent** (`UNIQUE(symbol, poll_ts)`); per-symbol error
isolation; ~23 GET calls per 11-symbol round, well under Kalshi's ~40-call/min budget.

**You cannot backfill this series — start collecting early.** Labels accrue at 3/day per
symbol; the I7 forward-validation read wants ≥50–100 (≈2–4 weeks across the liquid perps).

## Deploy (host-portable)

No FORTUNA prod box is assumed — wire the collector onto whatever always-on host you choose:
- **cron** (1/min): see `scripts/kairos.cron` → `kairos poll`.
- **systemd** (60s loop): see `scripts/kairos-collect.service` → `kairos collect --interval 60`.
- **macOS**: `nohup ./.venv/bin/kairos collect --interval 60 &` or a launchd plist.

Read-only everywhere: only GET requests; the client has no order/cancel methods.

## Canonical, leakage-safe schema
One row per funding interval; **every feature is an as-of/pre-settlement prior**, never
the realized next funding — nothing downstream can peek at the interval it predicts.

| col | meaning |
|-----|---------|
| `ts`,`venue`,`symbol` | settlement time (UTC), venue, ticker |
| `interval_hours` | inferred per-symbol (Kalshi 8h) — never hard-coded |
| `funding_now` | funding settled THIS interval (known at decision time) |
| `mark`,`reference` | mark price; spot index (live-captured; NaN in pure history) |
| `basis_bps` | `(mark−reference)/reference·1e4`, else de-clamped implied premium from `funding_now` |
| `funding_next` | **label**: funding settled next interval |

## What "pass / fail / null" means
- **Phase-A/B gate:** `nowcast_mae < base_mae` with the paired bootstrap CI fully below 0
  (`beats_base=True`) **and** nowcast net-of-realistic-fee APR > raw-carry APR
  (`beats_carry=True`), in some segment. Candidates, not a green light.
- **Phase-C gate (FORTUNA I7):** forward net edge ≥ 0, persistent over ≥50–100 intervals
  vs realized funding, before any capital. A clean null is a successful outcome.
- Selection runs on **forecast MAE / net APR, never gross APR** (gross overstates badly).

## Honest limits (from the memo)
- The synthetic demo proves the **wiring and the thesis on controlled data**; it is not
  evidence of real edge. Real edge needs Phase C on live independent basis.
- Kalshi perp fees are in a **$0 launch promo** (2026-06): on Kalshi today net≈gross. The
  gate runs against a *realistic* post-promo fee so a result is never a promo artifact.
- The edge that matters is the **carry / cross-market RV net of cost**, and it is a
  *decaying* risk premium — the binding constraints are fees, the negative-funding tail,
  and (for events) that the instrument barely exists yet. Maker-vs-taker fills swing the
  realistic edge from marginal-positive to ~0.
```
