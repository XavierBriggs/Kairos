# KAIROS data sources — validation & quality scorecard

Validated against **live pulls 2026-06-19** (not docs). All are Tier-1 primary venue APIs.
Graded for funding/basis RV use. Re-validate before trusting on a fast-moving venue.

## Scorecard

| Source | US-reachable, keyless | Funding (interval) | Mark+Index → basis | History | Tick/WS | Grade | Role |
|---|---|---|---|---|---|---|---|
| **Kalshi perps REST** | ✅ signed | ✅ estimate (8h); none on WS | ✅ reference + settlement_mark | ⚠️ ~2 wks (Jun-3 launch) | trades+book (WS) | **A** | primary / target venue |
| **Kalshi margin WS** | ✅ | ❌ **no funding/mark on ticker** | price/bid/ask only | live | ✅ price+book+trades | **B** | tick microstructure |
| **Hyperliquid** | ✅ | ✅ (1h) mark+oracle | ✅ | ✅ deep (500+) | — | **A** | core offshore |
| **OKX** | ✅ | ✅ + premium+interest (8h) | ✅ (premium direct) | ✅ (100/call) | — | **A** | core offshore |
| **Bitget** | ✅ | ✅ + interval field (8h) | ✅ index+last | ✅ | — | **B+** | breadth |
| **Gate** | ✅ | ✅ realized (8h) | ✅ index+last | ✅ | — | **B** | breadth |
| **Binance** (direct) | ❌ **451 geo-block US/AWS** | ✅ (8h) | ✅ | ✅ deepest | — | **D direct** | now wired via CoinGecko (live) |
| **Bybit** (direct) | ❌ **403 geo-block US/AWS** | ✅ (8h) | ✅ | ✅ | — | **D direct** | now wired via CoinGecko (live) |
| **CoinGecko `/derivatives`** | ✅ **keyless** | ✅ Binance+Bybit current (8h; % → fraction, /100) | ✅ price + index | ❌ **live-only (no history)** | — | **B** | vendor → Binance+Bybit (wired 2026-06-20) |

Also reachable from US, not wired: dYdX, Kraken Futures, KuCoin Futures (all 200, funding present).

**Update 2026-06-20:** Binance + Bybit added to the live cross-venue set via the **CoinGecko
`/derivatives`** vendor (keyless, US/AWS-reachable — verified HTTP 200 from the EC2 box). Their
direct futures APIs geo-block US/AWS, so the vendor is the path. `funding_rate` there is in
**percent/8h** (÷100 → fraction); basis is recomputed from `price` vs `index` for consistency.
**Live-only** — CoinGecko has no free funding *history*, so Binance/Bybit are excluded from the
historical backfill/carry analysis until their forward-captured `venue_funding` history is deep
enough. Cross-venue live set is now **7 venues**.

## Validation findings (what's true, grounded in live data)

1. **✅ Price/index integrity confirmed.** Live mark/index agree to ~0.3% across Kalshi
   ($62.5k via 6.25×0.0001 contract), OKX, Bitget, Gate, Hyperliquid. Data is real and consistent.

2. **⚠️ Raw funding is NOT comparable across venues; the basis is.** Each venue's funding adds a
   different mechanical part (Kalshi: no baseline + 1bp dead-zone + ±2% clamp; OKX/HL: ~+0.01%/8h
   interest baseline; clamps ±0.375% / ±4%/hr). So a raw funding spread (e.g. "HL +11% vs Kalshi 0%")
   is **mostly mechanical baseline, not edge** — HL's +11%/yr is its hourly interest baseline annualized.
   - The **clean cross-sectional signal is the basis (mark−index) in bp** — a level, comparable across
     venues regardless of funding interval. Live BTC basis dispersion was only **~6 bp** (Kalshi perp
     ~6bp richer vs its index than offshore) — i.e. **no fat premium RV right now**, despite the +11%
     raw-funding optics.
   - **Do NOT annualize an instantaneous basis** (an early KAIROS bug): the basis mean-reverts around 0
     intraday; ×1095 turns a −4.6bp snapshot into a nonsense −50%/yr. The sound mechanical-vs-premium
     split needs the **time series** (`venue_funding_hist`): mean ≈ baseline, std ≈ premium/demand
     variation. Live: HL mean +4.89%/yr but **std 72%** (huge premium swings); OKX/Bitget/Gate mean ~2.3–2.9%, std ~5–6%.

3. **✅ Interval heterogeneity handled.** Kalshi/OKX/Bitget/Gate = 8h; Hyperliquid = 1h. `funding_apr`
   annualizes each by its own interval — necessary (raw 8h vs 1h is off by 8×).

4. **⚠️ Kalshi WS carries no funding/mark.** The margin `ticker` channel = price/bid/ask/sizes only;
   funding & mark are REST-only (`funding_estimate`). KAIROS `ws_tick` stores price/bid/ask/sizes.
   WS host is the **dedicated margin host** `wss://external-api-margin-ws.kalshi.com/trade-api/ws/v2/margin`
   (not the event WS), signed path `/trade-api/ws/v2/margin`.

5. **⚠️ Gate funding has two fields** — use `funding_rate` (realized), never `funding_rate_indicative`.

6. **Minor:** OI reported in different units per venue (not normalized — funding RV doesn't need it);
   symbol mapping (BTC↔BTC-USDT-SWAP↔BTCUSDT↔BTC_USDT↔BTC) must be exact, low risk for majors.

## Funding decomposition (wired)
`dispersion` now splits each venue's funding into **baseline + premium** in matched units:
`premium_apr = funding_apr − baseline_apr`, where `baseline_apr = interest_rate × intervals/yr`.
- **OKX baseline is LIVE** (its `interestRate` field). Hyperliquid uses 0.01%/8h split hourly
  (0.0000125/hr). Bitget/Gate/Binance/Bybit are **assumed** the standard 0.01%/8h convention.
  Kalshi has **no baseline** (premium-only + 1bp dead-zone).
- This recovers the TWAP premium correctly (unlike annualizing an instantaneous basis). The
  `premium_apr` sign agrees with `basis_bps` sign as an internal check.
- Live BTC (2026-06-19): Kalshi premium ~0%, offshore premiums −2% to −11% (perps cheap to index);
  the +11% raw-funding optics is the interest baseline, not edge.

## Net guidance
- **Trust for price/basis:** Kalshi, Hyperliquid, OKX, Bitget, Gate.
- **Trust for raw funding per-venue:** yes. **For cross-venue *edge*:** use the decomposed
  **premium_apr** (+ basis_bps cross-check + historical funding std), NOT the raw funding-APR gap.
- **Core cross-venue set:** Kalshi + Hyperliquid + OKX (all A, US-reachable). Bitget/Gate add breadth.
- **Binance/Bybit:** dead weight from a US host; keep adapters for a non-US deploy.
