"""Read-only Kalshi WebSocket streamer — tick-level mark/funding + orderbook deltas.

Signed handshake reuses the RSA-PSS `_Signer` (sign GET on the WS path). Subscribes to
the `ticker` (mark/funding) and `orderbook_delta` channels for the perps and persists
`ws_tick` / `ws_book` rows. READ-ONLY: it only subscribes to market-data channels — no
order commands. Message parsing is a PURE function (`parse_message`) so it is unit-tested
without a socket; the exact perps WS host/channels are config vars verified live at build.
"""
from __future__ import annotations

import json
import time

from ..config import db_path
from .kalshi import _load_signer
from .store import connect, insert_ws_book, insert_ws_tick

# Perps use a DEDICATED margin WS host (distinct from the event WS), and the signed
# path includes the /margin suffix (per docs/research/venue/kinetics-perps-2026-06-10).
_WS_PROD = "wss://external-api-margin-ws.kalshi.com/trade-api/ws/v2/margin"
_WS_DEMO = "wss://external-api-margin-ws.demo.kalshi.co/trade-api/ws/v2/margin"
_WS_PATH = "/trade-api/ws/v2/margin"
_STALE_SECS = 90   # force a reconnect if no market data arrives for this long (half-open/keepalive-only)


def _num(x):
    if isinstance(x, dict):
        x = x.get("price")
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def parse_message(raw: str, recv_ts: int) -> tuple[str | None, object]:
    """Parse one WS frame into ('tick', row) | ('book', [rows]) | (None, None).

    Defensive: tolerates ack/subscribe/unknown frames and missing fields; always keeps
    the raw payload on ticks so no field is lost even if the perps schema shifts.
    """
    try:
        m = json.loads(raw)
    except (ValueError, TypeError):
        return None, None
    mtype = m.get("type")
    body = m.get("msg", m) if isinstance(m.get("msg"), dict) else m
    symbol = body.get("market_ticker") or body.get("ticker") or m.get("market_ticker")
    seq, sid = m.get("seq"), m.get("sid")

    if mtype == "ticker":
        # The margin ticker channel carries price/bid/ask/sizes — NOT funding or mark
        # (those are REST-only via funding_estimate). Verified live 2026-06-19.
        return "tick", {
            "symbol": symbol, "recv_ts": recv_ts,
            "price": _num(body.get("price")),
            "bid": _num(body.get("bid")), "ask": _num(body.get("ask")),
            "bid_size": _num(body.get("bid_size_fp")), "ask_size": _num(body.get("ask_size_fp")),
            "raw": raw[:4000],
        }

    if mtype in ("orderbook_snapshot", "orderbook_delta"):
        is_snap = 1 if mtype == "orderbook_snapshot" else 0
        rows: list[dict] = []
        if mtype == "orderbook_delta":
            rows.append({
                "symbol": symbol, "recv_ts": recv_ts, "seq": seq, "sid": sid,
                "side": body.get("side"), "price": _num(body.get("price")),
                "delta": _num(body.get("delta")), "is_snapshot": 0,
            })
        else:
            for side in ("yes", "no", "bids", "asks"):
                for lvl in body.get(side) or []:
                    if isinstance(lvl, (list, tuple)) and len(lvl) >= 2:
                        rows.append({
                            "symbol": symbol, "recv_ts": recv_ts, "seq": seq, "sid": sid,
                            "side": side, "price": _num(lvl[0]), "delta": _num(lvl[1]),
                            "is_snapshot": is_snap,
                        })
        return "book", rows

    return None, None


def _subscribe_cmd(tickers: list[str], channels: list[str]) -> str:
    return json.dumps({
        "id": 1, "cmd": "subscribe",
        "params": {"channels": channels, "market_tickers": tickers},
    })


def stream(tickers: list[str], demo: bool = False, channels: tuple[str, ...] = ("ticker", "orderbook_delta"),
           host: str | None = None, max_seconds: float | None = None) -> None:
    """Connect, subscribe, and persist ticks/book until interrupted (or max_seconds).

    Capped-exponential reconnect (0.5s -> 30s). Read-only — subscribe only.
    """
    from websocket import WebSocketTimeoutException, create_connection  # websocket-client

    signer = _load_signer(demo)
    url = host or (_WS_DEMO if demo else _WS_PROD)
    conn = connect(db_path(), autocommit=True)  # tiny per-write txns -> never lock-starve the collector
    print(f"KAIROS stream: {url}  channels={list(channels)}  tickers={len(tickers)}  signed={signer is not None}")
    t_start = time.time()
    backoff = 0.5
    n_tick = n_book = 0
    while True:
        if max_seconds and time.time() - t_start > max_seconds:
            print(f"stream done: {n_tick} ticks, {n_book} book rows")
            return
        try:
            # Re-sign on EVERY connect: the RSA-PSS handshake timestamp goes stale, and Kalshi
            # 401s a reused signature — reusing one signature 401-loops forever on reconnect.
            headers = signer.headers("GET", _WS_PATH) if signer else {}
            ws = create_connection(url, header=[f"{k}: {v}" for k, v in headers.items()], timeout=30)
            ws.settimeout(20)  # bounded recv so the data-staleness watchdog below can run
            ws.send(_subscribe_cmd(tickers, list(channels)))
            backoff = 0.5
            last_data = time.time()
            while True:
                if max_seconds and time.time() - t_start > max_seconds:
                    ws.close()
                    print(f"stream done: {n_tick} ticks, {n_book} book rows")
                    return
                # watchdog: a half-open / keepalive-only socket stays "alive" at TCP level while
                # market data silently stops and recv() blocks forever -> force a reconnect.
                if time.time() - last_data > _STALE_SECS:
                    ws.close()
                    raise TimeoutError(f"no market data for {_STALE_SECS}s")
                try:
                    raw = ws.recv()
                except WebSocketTimeoutException:
                    continue  # no frame in 20s; the staleness check forces a reconnect if it persists
                kind, payload = parse_message(raw, int(time.time() * 1000))
                if kind == "tick" and payload.get("symbol"):
                    if insert_ws_tick(conn, payload):
                        n_tick += 1
                    last_data = time.time()
                elif kind == "book" and payload:
                    n_book += insert_ws_book(conn, payload)
                    last_data = time.time()
                if (n_tick + n_book) % 50 == 0 and (n_tick + n_book) > 0:
                    conn.commit()
        except KeyboardInterrupt:
            conn.commit()
            print(f"\nstopped: {n_tick} ticks, {n_book} book rows")
            return
        except Exception as e:  # noqa: BLE001 - reconnect on any socket error
            conn.commit()
            print(f"ws reconnect after error: {str(e)[:120]}")
            try:
                time.sleep(backoff)
            except KeyboardInterrupt:
                return
            backoff = min(backoff * 2, 30.0)
