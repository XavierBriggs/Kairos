"""Kalshi WS message parsing — pure, no socket."""
import json

from kairos.data.kalshi_ws import parse_message


def test_ticker_message_to_tick():
    # real margin ticker shape (verified live): price/bid/ask/sizes, NO funding/mark
    raw = json.dumps({"type": "ticker", "sid": 1, "msg": {
        "market_ticker": "KXBTCPERP", "price": "6.2725", "bid": "6.2728", "ask": "6.2729",
        "bid_size_fp": "10.00", "ask_size_fp": "531.00"}})
    kind, row = parse_message(raw, 123)
    assert kind == "tick"
    assert row["symbol"] == "KXBTCPERP" and row["recv_ts"] == 123
    assert abs(row["price"] - 6.2725) < 1e-9
    assert abs(row["bid"] - 6.2728) < 1e-9 and abs(row["ask"] - 6.2729) < 1e-9
    assert row["bid_size"] == 10.0 and row["ask_size"] == 531.0


def test_orderbook_delta_to_book_row():
    raw = json.dumps({"type": "orderbook_delta", "seq": 5, "sid": 1,
                      "msg": {"market_ticker": "KXBTCPERP", "side": "yes", "price": "0.30", "delta": -4}})
    kind, rows = parse_message(raw, 1)
    assert kind == "book" and len(rows) == 1
    assert rows[0]["seq"] == 5 and rows[0]["delta"] == -4 and rows[0]["is_snapshot"] == 0


def test_orderbook_snapshot_expands_levels():
    raw = json.dumps({"type": "orderbook_snapshot", "seq": 1, "sid": 1, "msg": {
        "market_ticker": "X", "yes": [["0.30", "5"], ["0.29", "3"]], "no": [["0.70", "2"]]}})
    kind, rows = parse_message(raw, 1)
    assert kind == "book" and len(rows) == 3
    assert all(r["is_snapshot"] == 1 for r in rows)


def test_ack_and_garbage_return_none():
    assert parse_message(json.dumps({"type": "subscribed", "id": 1}), 1) == (None, None)
    assert parse_message("not json", 1) == (None, None)
