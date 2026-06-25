"""Hourly digest — health + the research signal (the `kairos digest` Slack post).

Turns raw row counts into something actionable: collection RATE (not totals), per-venue
health (catches a silent venue drop the deadman misses), forward-edge progress, the
cross-venue PREMIUM dispersion (the honest RV signal, mechanics stripped), and the Kalshi
funding cross-section (regime + extremes). Robust to empty/early data.
"""
from __future__ import annotations

import os
import shutil
import socket
import time

from .config import BacktestConfig, db_path
from .convergence import forward_edge
from .crossvenue import ASSET_MAP, US_VENUES, dispersion, index_offset_daily
from .data import store

_IPY8 = 365 * 24 / 8  # 8h periods per year -> annualize a per-8h funding rate


def _one(conn, sql, params=()):
    r = conn.execute(sql, params).fetchone()
    return r[0] if r and r[0] is not None else None


def _dur(s: float) -> str:
    s = int(s)
    d, h, m = s // 86400, (s % 86400) // 3600, (s % 3600) // 60
    return f"{d}d{h}h" if d else (f"{h}h{m}m" if h else f"{m}m")


def _uptime() -> str | None:
    try:
        with open("/proc/uptime") as f:
            return _dur(float(f.read().split()[0]))
    except OSError:
        return None


def _disk(path) -> tuple[float | None, int | None]:
    try:
        t, u, free = shutil.disk_usage(os.path.dirname(str(path)) or "/")
        return u / t * 100.0, free
    except OSError:
        return None, None


def _short(sym: str) -> str:
    return sym.replace("KX", "").replace("PERP", "")


def digest_text(conn) -> str:
    now_ms = int(time.time() * 1000)
    hr = now_ms - 3_600_000
    out: list[str] = []

    # --- header: host / uptime / disk + rough headroom -----------------------
    pct, free = _disk(db_path())
    head = f"📊 KAIROS digest [{socket.gethostname()}]"
    up = _uptime()
    if up:
        head += f" · up {up}"
    if pct is not None:
        head += f" · /data {pct:.0f}%"
    out.append(head)

    # --- collection RATE over the last hour ----------------------------------
    snaps = _one(conn, "SELECT COUNT(*) FROM snapshot WHERE poll_ts>?", (hr,)) or 0
    trades = _one(conn, "SELECT COUNT(*) FROM trade WHERE created_time>"
                        "strftime('%Y-%m-%dT%H:%M:%S','now','-1 hour')") or 0
    book = _one(conn, "SELECT COUNT(*) FROM ws_book WHERE recv_ts>?", (hr,)) or 0
    errs = _one(conn, "SELECT COALESCE(SUM(n_err),0) FROM poll_run WHERE run_ts>?", (hr,)) or 0
    head_days = ""
    if free is not None and book:
        gb_day = book * 24 * 80 / 1e9  # ~80 bytes/book row
        if gb_day > 0:
            head_days = f"  (~{free / 1e9 / gb_day:.0f}d book headroom)"
    out.append(f"last 1h: +{snaps} snaps · +{trades} trades · +{book:,} book ({book // 3600}/s) · errs {errs}{head_days}")

    # --- per-venue health (the silent-failure catcher) -----------------------
    latest = _one(conn, "SELECT MAX(poll_ts) FROM venue_funding")
    if latest:
        out.append("venue health (last round):")
        for v in US_VENUES:
            seen = _one(conn, "SELECT MAX(poll_ts) FROM venue_funding WHERE venue=?", (v,))
            if not seen:
                out.append(f"  {v:11} ✗ no data")
                continue
            row = conn.execute("SELECT funding_apr, basis_bps FROM venue_funding "
                               "WHERE venue=? AND asset='BTC' ORDER BY poll_ts DESC LIMIT 1", (v,)).fetchone()
            fa = f"{row[0] * 100:+.1f}%/yr" if row and row[0] is not None else "n/a"
            ba = f"{row[1]:+.1f}bp" if row and row[1] is not None else "n/a"
            if seen == latest:
                n = _one(conn, "SELECT COUNT(DISTINCT asset) FROM venue_funding WHERE venue=? AND poll_ts=?", (v, latest)) or 0
                out.append(f"  {v:11} ✓ {n} assets · BTC fund {fa}  basis {ba}")
            else:
                out.append(f"  {v:11} ✗ STALE {(now_ms - seen) // 60000}m · last BTC fund {fa}")

    # --- forward-edge progress / verdict -------------------------------------
    lab = store.build_labeled_intervals(conn, "KXBTCPERP")
    n = len(lab)
    if n < 50:
        out.append(f"forward edge: BTC {n}/50 labeled (~{max(0.0, (50 - n) / 3.0):.0f}d to first read)")
    else:
        fe = forward_edge(lab, BacktestConfig())
        out.append(f"forward edge: BTC n={fe.n} nowcast {fe.nowcast_net_apr * 100:+.1f}%/yr "
                   f"CI[{fe.net_ci_apr[0] * 100:+.1f},{fe.net_ci_apr[1] * 100:+.1f}] "
                   f"edge={'YES' if fe.edge_positive else 'no'}")

    # --- cross-venue widest PREMIUM dispersion (the honest RV signal) ---------
    best = None
    for a in ASSET_MAP:
        d = dispersion(conn, a)
        if d.empty or "premium_apr_%" not in d.columns:
            continue
        p = d["premium_apr_%"].dropna()
        if len(p) >= 2:
            sp = float(p.max() - p.min())
            if best is None or sp > best[1]:
                best = (a, sp, d)
    if best:
        a, sp, d = best
        t = d.sort_values("premium_apr_%", ascending=False)
        hi, lo = t.iloc[0], t.iloc[-1]
        out.append(f"x-venue premium (mech stripped) — widest: {a} {sp:.1f}% "
                   f"({hi['venue']} {hi['premium_apr_%']:+.1f} vs {lo['venue']} {lo['premium_apr_%']:+.1f})")

    # --- Kalshi-vs-offshore index offset (is the "Kalshi rich" gap structural or noise?) ---
    # The price test showed the perps are ~co-priced; the funding gap is an index-construction
    # difference. Track its daily offset: STABLE => structural (small real carry); MEAN-REVERTING
    # => lag/noise (no edge). This is the discriminator that decides if the signal is tradeable.
    od = index_offset_daily(conn, "XRP", days=10)
    if not od.empty and len(od) >= 2:
        off = od["offset_bps"]
        structural = off.std() < abs(off.mean()) * 0.5 and abs(off.mean()) > 1.0
        out.append(f"XRP index offset (Kalshi−offshore basis): today {off.iloc[-1]:+.1f}bp · "
                   f"{len(off)}d {off.mean():+.1f}±{off.std():.1f}bp · "
                   f"{'structural' if structural else 'noisy/mean-reverting'}")

    # --- Kalshi funding cross-section (regime + extremes) --------------------
    rows = conn.execute("SELECT symbol, funding_est FROM snapshot s "
                        "WHERE poll_ts=(SELECT MAX(poll_ts) FROM snapshot WHERE symbol=s.symbol) "
                        "AND funding_est IS NOT NULL").fetchall()
    if rows:
        ann = sorted(((_short(s), f * _IPY8 * 100) for s, f in rows), key=lambda x: x[1])
        neg = sum(1 for _, v in ann if v < 0)
        out.append(f"Kalshi funding x-section: richest {ann[-1][0]} {ann[-1][1]:+.0f}%/yr, "
                   f"cheapest {ann[0][0]} {ann[0][1]:+.0f}%/yr; {neg}/{len(ann)} neg")
    return "\n".join(out)
