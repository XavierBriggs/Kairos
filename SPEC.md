# KAIROS — spec (data model + invariants)

One page. Research harness for perp funding/basis RV. Not in the Rust workspace, not on a
money path. See README.md (usage), SOURCES.md (data-quality), and the research memo
`docs/research/2026-06-18-perpetual-futures-modeling.md` (the thesis).

## Invariants
- **Read-only.** Every venue client issues GET / WS-subscribe only. No order/cancel/amend
  methods exist anywhere. (Grep gate: no `session.post/put/delete` to a trading path; no
  `place/cancel/amend/order` defs except `orderbook`.)
- **Idempotent storage.** Every table has a UNIQUE key; writes are INSERT-OR-IGNORE or upsert.
  Re-running a poll never double-counts.
- **Leakage-safe labels.** In `build_labeled_intervals`, every feature is an as-of value
  STRICTLY before the settlement it predicts; the label is the next settlement's funding.
- **Per-symbol funding interval.** Never hard-code 8h; the labeler infers it from settlement
  gaps; `funding_apr` annualizes each venue by its own interval.
- **Secrets via env only** (`KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH` from `<repo>/.env`);
  never logged or committed.

## SQLite tables (`data/kairos/kairos.db`, WAL)
| table | grain | purpose |
|---|---|---|
| `snapshot` | (symbol, poll_ts) | **Kalshi-only, rich** forward capture: mark/reference/settlement+liq mark, top-10 L2 book, derived basis/spread/microprice/imbalance, OI, funding estimate, raw JSON. Feeds the **nowcast / forward_edge**. |
| `funding_settlement` | (symbol, funding_time) | Kalshi official 8h settled funding+mark. The forward-edge **labels**. |
| `venue_funding` | (venue, symbol, poll_ts) | **Thin, normalized cross-venue** funding rows (Kalshi + offshore): funding_rate, interval, funding_apr, basis_bps. Feeds **dispersion**. |
| `venue_funding_hist` | (venue, symbol, funding_time) | Offshore historical funding (months/years) → backtest cross-venue carry/RV now. Feeds `hist_means`. |
| `trade` | (trade_id) | Kalshi taker tape (price, count, taker_side). |
| `candle` | (symbol, period, end_ts) | Kalshi OHLCV + OI history. |
| `ws_tick` | (symbol, recv_ts) | WS tick: price/bid/ask/sizes (NO funding/mark — REST-only). |
| `ws_book` | append | WS orderbook snapshot/delta rows (seq/sid/side/price/delta). |
| `poll_run` | append | Collector health/audit. |

## Why Kalshi funding lives in BOTH `snapshot` and `venue_funding` (not redundant)
- `snapshot` is the **deep, Kalshi-only forward feed** with L2/microstructure and the independent
  mark−reference basis — built for the **single-venue nowcast** (`forward_edge`).
- `venue_funding` is a **thin, uniform row** (funding + basis) repeated across venues — built for the
  **cross-venue dispersion** comparison, where Kalshi must appear alongside offshore on equal footing.
- Different grain, different consumer. The Kalshi row in `venue_funding` is a derived convenience;
  `snapshot` is the source of truth for Kalshi microstructure.

## Signals & their honest readouts
- **Single-venue nowcast** (`forward` / `convergence.forward_edge`): does the live independent basis
  predict next funding better than persistence, net of fee. Needs forward capture (Kalshi history has
  no stored index → nowcast degenerates retrospectively). Gate = `edge_positive` over ≥50–100 labels.
- **Cross-venue RV** (`dispersion` / `hist_means`): funding is decomposed into **baseline + premium**
  (`premium_apr = funding_apr − baseline_apr`, baseline from each venue's interest rate — OKX live,
  others by convention, Kalshi 0). Trade the **premium dispersion** (+ basis_bps cross-check + historical
  funding std), NOT the raw funding-APR gap (mostly mechanical baseline; see SOURCES.md).
- Selection on forecast error / net-of-fee, **never gross APR**.

## Data reachability (US host)
Reachable keyless: Kalshi, Hyperliquid, OKX, Bitget, Gate. Geo-blocked: Binance (451), Bybit (403)
— adapters kept for a non-US host. The collector degrades gracefully (a dead venue → None, round survives).
