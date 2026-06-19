"""Forward-capture poller (Phase C) — read-only, idempotent, host-portable.

`poll_once` does ONE round: list all active perps in a single `markets()` call, then
per symbol pull the funding estimate + L2 orderbook, derive basis/spread/microprice/
imbalance, and insert a snapshot row. Official 8h settlements are refreshed only when a
new one is actually due (self-gated against the DB), so the funding endpoint is hit
~once/symbol/8h, not every poll — keeping a 11-symbol round at ~23 calls, well under the
~40-call/min budget. `collect_loop` repeats with jitter and survives transient errors.

Read-only by construction: only the GET methods of the Kalshi client are used.
"""
from __future__ import annotations

import json
import random
import time

from .basis import basis_bps
from .config import CollectorConfig
from .data import store
from .data.kalshi import KalshiPerpClient, _price, _to_float


def _ts(field) -> int | None:
    return field.get("ts_ms") if isinstance(field, dict) else None


def _best(levels: list, side: str) -> tuple[float | None, float | None]:
    """Best (price, size) from [[price,size], ...]; best bid = max price, best ask = min."""
    parsed = [(_to_float(p), _to_float(s)) for p, s in levels] if levels else []
    parsed = [(p, s) for p, s in parsed if p == p]  # drop NaN prices
    if not parsed:
        return None, None
    return (max if side == "bid" else min)(parsed, key=lambda x: x[0])


def derive(market: dict, funding_est: dict, book: dict, poll_ts: int, depth: int) -> dict:
    """Market + funding estimate + orderbook -> one snapshot row (all schema columns)."""
    reference = _price(market.get("reference_price"))
    settlement_mark = _price(market.get("settlement_mark_price"))
    mark = settlement_mark if settlement_mark == settlement_mark else reference
    b = basis_bps(mark, reference) if reference and reference == reference and mark == mark else float("nan")

    ob = book.get("orderbook", book) if isinstance(book, dict) else {}
    bids, asks = ob.get("bids") or [], ob.get("asks") or []
    best_bid, bid_sz = _best(bids, "bid")
    best_ask, ask_sz = _best(asks, "ask")
    spread_bps = microprice = imbalance_l1 = None
    if best_bid and best_ask:
        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 1e4 if mid else None
        if bid_sz and ask_sz and (bid_sz + ask_sz) > 0:
            microprice = (best_bid * ask_sz + best_ask * bid_sz) / (bid_sz + ask_sz)
            imbalance_l1 = bid_sz / (bid_sz + ask_sz)

    return {
        "symbol": market.get("ticker"),
        "poll_ts": poll_ts,
        "mark": mark,
        "reference": reference,
        "settlement_mark": settlement_mark,
        "liquidation_mark": _price(market.get("liquidation_mark_price")),
        "last_price": _to_float(market.get("price")),
        "mark_ts": _ts(market.get("settlement_mark_price")),
        "reference_ts": _ts(market.get("reference_price")),
        "basis_bps": b,
        "best_bid": best_bid,
        "best_ask": best_ask,
        "spread_bps": spread_bps,
        "microprice": microprice,
        "imbalance_l1": imbalance_l1,
        "funding_est": _to_float(funding_est.get("funding_rate")),
        "computed_time": funding_est.get("computed_time"),
        "next_funding_time": funding_est.get("next_funding_time"),
        "open_interest": _to_float(market.get("open_interest")),
        "volume_24h": _to_float(market.get("volume_24h")),
        "leverage_estimate": _to_float(market.get("leverage_estimate")),
        "book_json": json.dumps({"bids": bids[:depth], "asks": asks[:depth]}),
        "raw_market": json.dumps(market),
        "raw_funding_est": json.dumps(funding_est),
    }


def _settlement_due(conn, symbol: str, interval_h: int, now_s: int) -> bool:
    """True if no settlement stored, or the newest is ~older than one interval (a new 8h
    rate has likely posted) — so we hit the funding endpoint only when it matters."""
    df = store.load_settlements(conn, symbol)
    if df.empty:
        return True
    newest = df["t"].iloc[-1].timestamp()
    return (now_s - newest) >= (interval_h * 3600 - 600)  # 10-min slack before the boundary


def poll_once(client: KalshiPerpClient, conn, cfg: CollectorConfig) -> tuple[int, list[str]]:
    """One read-only round across all (or configured) active perps. Returns (n_ok, errors)."""
    t0 = time.time()
    poll_ts = int(t0 * 1000)
    now_s = int(t0)
    all_markets = {m.get("ticker"): m for m in client.markets(status="active")}
    symbols = list(cfg.symbols) if cfg.symbols else list(all_markets.keys())
    n_ok, errors = 0, []
    for sym in symbols:
        try:
            market = all_markets.get(sym) or client.market(sym)
            fe = client.funding_estimate(sym)
            book = client.orderbook(sym, depth=cfg.depth)
            store.insert_snapshot(conn, derive(market, fe, book, poll_ts, cfg.depth))
            if cfg.capture_trades:
                store.insert_trades(conn, sym, client.trades(sym, limit=cfg.trade_limit))
            if cfg.settlement_refresh and _settlement_due(conn, sym, cfg.funding_interval_hint, now_s):
                recs = client.funding_history(
                    sym, start_ts=now_s - cfg.settlement_lookback_days * 86400, end_ts=now_s
                )
                store.upsert_settlements(conn, sym, recs)
            n_ok += 1
        except Exception as e:  # noqa: BLE001 - isolate per-symbol so one bad symbol can't kill the round
            errors.append(f"{sym}: {e}")
    if cfg.capture_crossvenue:
        try:
            from .crossvenue import collect_live
            collect_live(conn, client)
        except Exception as e:  # noqa: BLE001 - offshore venues are best-effort
            errors.append(f"crossvenue: {e}")
    store.record_run(
        conn, poll_ts, len(symbols), n_ok, len(errors), int((time.time() - t0) * 1000),
        "; ".join(errors),
    )
    return n_ok, errors


def collect_loop(client: KalshiPerpClient, conn, cfg: CollectorConfig) -> None:
    """Poll every cfg.interval_secs (+jitter) until interrupted. Survives transient errors."""
    print(f"KAIROS collect: every {cfg.interval_secs}s, depth={cfg.depth}, "
          f"symbols={cfg.symbols or 'ALL active'}. Ctrl-C to stop.")
    while True:
        try:
            n_ok, errs = poll_once(client, conn, cfg)
            stamp = time.strftime("%H:%M:%S", time.gmtime())
            msg = f"[{stamp}Z] {n_ok} ok"
            if errs:
                msg += f", {len(errs)} err ({errs[0][:80]})"
            print(msg)
        except KeyboardInterrupt:
            print("\nstopped.")
            return
        except Exception as e:  # noqa: BLE001 - a whole-round failure shouldn't kill the loop
            print(f"round error: {e}")
        try:
            time.sleep(cfg.interval_secs + random.uniform(0, cfg.jitter_secs))
        except KeyboardInterrupt:
            print("\nstopped.")
            return
