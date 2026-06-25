"""Cross-venue funding relative value — with PREMIUM normalization.

Collects the SAME asset's funding across Kalshi + US-reachable offshore venues
(Hyperliquid, OKX, Bitget, Gate) into `venue_funding`, and reads out two things:

  - funding_apr   : the raw, annualized funding you'd actually COLLECT (tradeable, but
                    includes each venue's mechanical interest baseline / dead-zone / clamp).
  - premium_apr   : the PREMIUM-driven component only (basis = mark−index, annualized),
                    free of the mechanical baseline — the clean cross-venue demand signal.

Comparing raw funding across venues OVERSTATES edge (e.g. Hyperliquid's ~+11%/yr is mostly
its interest baseline, not premium). The premium dispersion is the honest RV; the funding
dispersion is what nets to your account. We show both. Binance/Bybit are US-geo-blocked and
omitted from the default set (offshore module keeps them for a non-US host).
"""
from __future__ import annotations

import time

import pandas as pd

from .basis import basis_bps as _basis_bps
from .data import offshore, store
from .data.kalshi import KalshiPerpClient, _price, _to_float

_HOURS_PER_YEAR = 365.0 * 24.0

# asset -> per-venue symbol. Default cross-venue set is US-reachable + keyless.
ASSET_MAP: dict[str, dict[str, str]] = {
    a: {"kalshi": f"KX{a}PERP", "okx": f"{a}-USDT-SWAP", "bitget": f"{a}USDT",
        "gate": f"{a}_USDT", "hyperliquid": a, "binance": f"{a}USDT", "bybit": f"{a}USDT"}
    for a in ("BTC", "ETH", "SOL", "XRP", "DOGE", "LTC", "LINK", "BCH")
}
US_VENUES = ("kalshi", "hyperliquid", "okx", "bitget", "gate", "binance", "bybit")
# Clean direct-API offshore venues for the index-offset tracker. Binance/Bybit are EXCLUDED:
# their basis comes via the CoinGecko vendor (cached/lagged price vs index), so their basis_bps
# is noise (we saw -49/-55bp artifacts) and would corrupt the offshore reference.
_CLEAN_OFFSHORE = ("hyperliquid", "okx", "bitget", "gate")


def _kalshi_row(client: KalshiPerpClient, asset: str, ticker: str, poll_ts: int) -> dict | None:
    try:
        fe = client.funding_estimate(ticker)
        m = client.market(ticker)
    except Exception:  # noqa: BLE001 - one venue down must not kill the round
        return None
    rate = _to_float(fe.get("funding_rate"))
    mark, ref = _price(m.get("settlement_mark_price")), _price(m.get("reference_price"))
    b = _basis_bps(mark, ref) if ref and ref == ref and mark == mark else None
    return {
        "venue": "kalshi", "symbol": ticker, "asset": asset, "poll_ts": poll_ts,
        "funding_rate": rate, "interval_hours": 8.0,
        "funding_apr": offshore.annualize_funding(rate, 8.0),
        "mark": mark, "index_price": ref, "basis_bps": b,
        "open_interest": _to_float(m.get("open_interest")),
        "next_funding_time": fe.get("next_funding_time"),
        "interest_rate": 0.0,   # Kalshi has NO interest baseline (premium-only + dead-zone)
    }


def collect_live(conn, client: KalshiPerpClient, assets: list[str] | None = None) -> int:
    """One cross-venue funding round across `assets` (US-reachable venues). Idempotent."""
    poll_ts = int(time.time() * 1000)
    assets = assets or list(ASSET_MAP)
    hl = offshore.hyperliquid_all()         # ALL coins in one call
    cg = offshore.coingecko_perps(assets)   # Binance + Bybit via vendor, one call (US geo-blocks direct)
    rows: list[dict] = []
    for asset in assets:
        m = ASSET_MAP.get(asset)
        if not m:
            continue
        for r in (_kalshi_row(client, asset, m["kalshi"], poll_ts),
                  offshore.okx_live(m["okx"]),
                  offshore.bitget_live(m["bitget"]),
                  offshore.gate_live(m["gate"]),
                  hl.get(m["hyperliquid"]),
                  cg.get(("binance", asset)),
                  cg.get(("bybit", asset))):
            if r:
                rows.append({**r, "asset": asset, "poll_ts": poll_ts})
    return store.insert_venue_funding(conn, rows)


def dispersion(conn, asset: str) -> pd.DataFrame:
    """Latest funding by venue for `asset`. Two honest, comparable views:

      - funding_apr_% : raw annualized funding you'd COLLECT (interval-annualized; includes
                        each venue's mechanical interest baseline / dead-zone / clamp).
      - basis_bps     : the instantaneous PREMIUM LEVEL (mark−index), in bp — the clean
                        cross-sectional demand signal, comparable across venues regardless
                        of funding interval. (NOT annualized — an instantaneous basis is a
                        level, not a per-interval rate; annualizing a snapshot is meaningless.)

    Separating the persistent mechanical baseline from the time-varying premium needs the
    TIME SERIES (venue_funding_hist), not a snapshot — see `hist_means`.
    """
    df = store.load_venue_funding(conn, asset)
    if df.empty:
        return df
    latest = df.sort_values("poll_ts").groupby("venue").tail(1).copy()
    ipy = _HOURS_PER_YEAR / latest["interval_hours"]
    # SOUND decomposition (matched units — both are per-interval funding-rate quantities):
    #   funding = baseline (interest) + premium (demand).  premium = funding - baseline.
    # This recovers the TWAP premium correctly; do NOT use the instantaneous basis for it.
    latest["baseline_apr"] = latest["interest_rate"] * ipy
    latest["premium_apr"] = latest["funding_apr"] - latest["baseline_apr"]
    latest["funding_apr_%"] = (latest["funding_apr"] * 100).round(2)
    latest["baseline_apr_%"] = (latest["baseline_apr"] * 100).round(2)
    latest["premium_apr_%"] = (latest["premium_apr"] * 100).round(2)
    latest["basis_bps"] = latest["basis_bps"].round(3)
    cols = ["venue", "interval_hours", "funding_apr_%", "baseline_apr_%", "premium_apr_%", "basis_bps"]
    return latest[cols].sort_values("premium_apr_%", ascending=False).reset_index(drop=True)


def hist_means(conn, asset: str) -> pd.DataFrame:
    """Mean realized funding per venue over the backfilled history, annualized. The
    spread in MEANS is mostly the mechanical baseline; the time-VARIATION (std) is where
    the premium/demand signal lives. This is the methodologically-sound view a single
    snapshot cannot give."""
    df = pd.read_sql_query(
        "SELECT venue, funding_rate, interval_hours FROM venue_funding_hist WHERE asset = ?",
        conn, params=(asset,),
    )
    if df.empty:
        return df
    df["apr"] = df["funding_rate"] * (_HOURS_PER_YEAR / df["interval_hours"])
    g = df.groupby("venue")["apr"].agg(["mean", "std", "count"]).reset_index()
    g["mean_apr_%"] = (g["mean"] * 100).round(2)
    g["std_apr_%"] = (g["std"] * 100).round(2)
    return g[["venue", "mean_apr_%", "std_apr_%", "count"]].sort_values("mean_apr_%", ascending=False)


def index_offset_daily(conn, asset: str, days: int = 14) -> pd.DataFrame:
    """Daily Kalshi-minus-offshore BASIS offset for `asset` — the discriminator between a
    STRUCTURAL index difference and lag/noise.

    The live price test (2026-06-24) showed the cross-venue perp PRICES are ~equal while
    Kalshi's INDEX reads ~10-15bp below the offshore oracles — so the "Kalshi rich vs offshore
    cheap" funding gap is mostly an index-construction artifact, not a tradeable price gap.
    Since the perps are ~co-priced, (kalshi_basis - offshore_basis) ≈ -(index offset) to first
    order, and it is UNIT-FREE (no per-asset contract-scale needed). If this offset is STABLE
    day-over-day it is a structural index difference (a small real carry may survive); if it
    MEAN-REVERTS it is just lag/noise (no edge). Offshore = clean direct venues only.
    """
    cutoff = int((time.time() - days * 86400) * 1000)
    placeholders = ",".join("?" for _ in _CLEAN_OFFSHORE)
    df = pd.read_sql_query(
        f"SELECT venue, poll_ts, basis_bps FROM venue_funding "
        f"WHERE asset=? AND poll_ts>? AND basis_bps IS NOT NULL "
        f"AND venue IN ('kalshi',{placeholders})",
        conn, params=(asset, cutoff, *_CLEAN_OFFSHORE),
    )
    if df.empty:
        return df
    df["day"] = pd.to_datetime(df["poll_ts"], unit="ms").dt.strftime("%Y-%m-%d")
    df["side"] = (df["venue"] == "kalshi").map({True: "kalshi", False: "offshore"})
    g = df.groupby(["day", "side"])["basis_bps"].mean().unstack("side")
    if "kalshi" not in g.columns or "offshore" not in g.columns:
        return pd.DataFrame()
    g = g.dropna(subset=["kalshi", "offshore"])
    g["kalshi_bps"] = g["kalshi"].round(2)
    g["offshore_bps"] = g["offshore"].round(2)
    g["offset_bps"] = (g["kalshi"] - g["offshore"]).round(2)
    return g.reset_index()[["day", "kalshi_bps", "offshore_bps", "offset_bps"]]


def backfill_hist(conn, assets: list[str] | None = None, limit: int = 500,
                  hl_days: int = 21) -> dict[str, int]:
    """Backfill offshore historical funding into venue_funding_hist. Returns per-venue counts.

    Hyperliquid's fundingHistory returns up to 500 records FORWARD from startTime, so to get
    RECENT (not 2023-launch) data we anchor startTime ~hl_days back (hourly -> ~500 recs ≈ 21d
    ending near now)."""
    assets = assets or list(ASSET_MAP)
    hl_start = int((time.time() - hl_days * 86400) * 1000)
    counts = {"okx": 0, "bitget": 0, "gate": 0, "hyperliquid": 0}
    for asset in assets:
        m = ASSET_MAP.get(asset)
        if not m:
            continue
        for venue, recs in (
            ("okx", offshore.okx_history(m["okx"], limit=min(limit, 100))),
            ("bitget", offshore.bitget_history(m["bitget"], limit=min(limit, 100))),
            ("gate", offshore.gate_history(m["gate"], limit=limit)),
            ("hyperliquid", offshore.hyperliquid_history(m["hyperliquid"], start_ms=hl_start)),
        ):
            counts[venue] += store.upsert_venue_funding_hist(conn, [{**r, "asset": asset} for r in recs])
    return counts
