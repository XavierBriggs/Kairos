import pandas as pd

from kairos.data import store


def _conn(tmp_path):
    return store.connect(tmp_path / "t.db")


def _snap(symbol, poll_ts, basis, mark=60000.0, ref=60000.0):
    return {"symbol": symbol, "poll_ts": poll_ts, "basis_bps": basis, "mark": mark, "reference": ref}


def test_snapshot_insert_is_idempotent(tmp_path):
    conn = _conn(tmp_path)
    assert store.insert_snapshot(conn, _snap("KXBTCPERP", 1000, 5.0)) is True
    # same (symbol, poll_ts) -> ignored, returns False, no dup
    assert store.insert_snapshot(conn, _snap("KXBTCPERP", 1000, 5.0)) is False
    assert store.insert_snapshot(conn, _snap("KXBTCPERP", 2000, 6.0)) is True
    cov = store.coverage(conn)
    assert int(cov.loc[cov.symbol == "KXBTCPERP", "snapshots"].iloc[0]) == 2


def test_settlement_upsert_dedupes_and_updates(tmp_path):
    conn = _conn(tmp_path)
    recs = [{"funding_time": "2026-06-01T04:00:00Z", "funding_rate": "0.001", "mark_price": "60000"}]
    store.upsert_settlements(conn, "KXBTCPERP", recs)
    # re-upsert same funding_time with a corrected rate -> updated, not duplicated
    recs2 = [{"funding_time": "2026-06-01T04:00:00Z", "funding_rate": "0.002", "mark_price": "60000"}]
    store.upsert_settlements(conn, "KXBTCPERP", recs2)
    df = store.load_settlements(conn, "KXBTCPERP")
    assert len(df) == 1
    assert abs(df["funding_rate"].iloc[0] - 0.002) < 1e-12


def _ms(iso):
    return int(pd.Timestamp(iso).timestamp() * 1000)


def test_build_labeled_intervals_is_leakage_safe(tmp_path):
    conn = _conn(tmp_path)
    # three settlements at the 8h boundaries
    store.upsert_settlements(conn, "KXBTCPERP", [
        {"funding_time": "2026-06-01T04:00:00Z", "funding_rate": "0.0010", "mark_price": "60000"},
        {"funding_time": "2026-06-01T12:00:00Z", "funding_rate": "0.0005", "mark_price": "60100"},
        {"funding_time": "2026-06-01T20:00:00Z", "funding_rate": "-0.0003", "mark_price": "59900"},
    ])
    # snapshots inside each window; the LAST before T is the as-of basis
    store.insert_snapshot(conn, _snap("KXBTCPERP", _ms("2026-06-01T07:00:00Z"), 9.0))
    store.insert_snapshot(conn, _snap("KXBTCPERP", _ms("2026-06-01T11:30:00Z"), 6.0))   # as-of for T=12:00
    store.insert_snapshot(conn, _snap("KXBTCPERP", _ms("2026-06-01T19:30:00Z"), 3.0))   # as-of for T=20:00

    df = store.build_labeled_intervals(conn, "KXBTCPERP")
    assert len(df) == 2
    r0 = df.iloc[0]   # interval ending at 12:00
    assert abs(r0["funding_now"] - 0.0010) < 1e-12      # rate at the previous settlement (04:00)
    assert abs(r0["funding_next"] - 0.0005) < 1e-12     # label = rate at 12:00
    assert abs(r0["basis_bps"] - 6.0) < 1e-9            # last snapshot strictly before 12:00
    r1 = df.iloc[1]   # interval ending at 20:00
    assert abs(r1["funding_now"] - 0.0005) < 1e-12
    assert abs(r1["funding_next"] - (-0.0003)) < 1e-12
    assert abs(r1["basis_bps"] - 3.0) < 1e-9


def test_build_labeled_intervals_empty_when_no_data(tmp_path):
    conn = _conn(tmp_path)
    assert store.build_labeled_intervals(conn, "KXBTCPERP").empty
