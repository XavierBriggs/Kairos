"""KAIROS command line: backtest / markets / funding.

  kairos backtest                 Phase-A synthetic demo (no keys, no network)
  kairos backtest --real --symbol KXBTCPERP --days 60   Phase-B on REAL Kalshi funding
  kairos markets                  live READ-ONLY: list perps + funding estimates (confirms creds)
  kairos funding --symbol KXBTCPERP --days 30 --out f.csv   pull real funding history
"""
from __future__ import annotations

import argparse
import sys
import time

from .config import BacktestConfig, FeeConfig


def _cfg(promo: bool) -> BacktestConfig:
    return BacktestConfig(fees=FeeConfig(promo=promo))


def _cmd_backtest(args: argparse.Namespace) -> int:
    from .backtest import format_report, headline, run_backtest

    cfg = _cfg(args.promo)
    if args.real:
        from .data.kalshi import KalshiPerpClient, funding_history_to_schema

        client = KalshiPerpClient(demo=args.demo)
        now = int(time.time())
        recs = client.funding_history(args.symbol, start_ts=now - args.days * 86400, end_ts=now)
        df = funding_history_to_schema(recs, args.symbol, cfg.funding)
        src = (
            f"REAL Kalshi funding [{args.symbol}, {len(df):,} intervals, ~{args.days}d, "
            f"signed={client.signed}, promo={args.promo}]"
        )
    else:
        from .synth import make_series

        df = make_series(n=args.n, seed=args.seed)
        src = f"SYNTHETIC demo ({len(df):,} intervals, seed={args.seed}, promo={args.promo})"

    scored, report = run_backtest(cfg, df)
    print(f"\nKAIROS backtest — {src}, scored {len(scored):,} intervals\n")
    print(format_report(report))
    print("\n" + headline(scored, cfg))
    print(
        "\nRead: beats_base=True (mae_ci_bp fully <0) => the basis nowcast forecasts funding "
        "more sharply than persistence. beats_carry=True => trading the nowcast nets more than "
        "always-collecting raw carry, after fees. Selection is on MAE / net APR, never gross."
    )
    if args.real:
        print(
            "NOTE (--real): Kalshi funding history has no stored index, so basis is reconstructed "
            "from funding and the nowcast DEGENERATES to no_change here by construction. Phase B "
            "validates funding autocorrelation + carry economics; the independent-basis nowcast is "
            "only testable forward (poll mark+reference live -> convergence.forward_edge)."
        )
    if args.out:
        scored.to_csv(args.out, index=False)
        print(f"\nScored rows -> {args.out}")
    return 0


def _cmd_markets(args: argparse.Namespace) -> int:
    from .data.kalshi import KalshiPerpClient, _price

    client = KalshiPerpClient(demo=args.demo)
    status = client.exchange_status()
    print(f"exchange: {status}    signed={client.signed}    host={client.host}")
    markets = client.markets(status="active")
    if not markets:
        print("No active perp markets returned.", file=sys.stderr)
        return 1
    print(f"\n{len(markets)} active perp market(s):  (basis_bp = (mark−reference)/reference)")
    for m in markets[: args.limit]:
        tk = m.get("ticker", "?")
        mark = _price(m.get("settlement_mark_price"))
        ref = _price(m.get("reference_price"))
        basis = (mark - ref) / ref * 1e4 if ref and ref == ref else float("nan")
        try:
            est = client.funding_estimate(tk).get("funding_rate")
        except Exception as e:  # noqa: BLE001 - read-only probe, report and continue
            est = f"(err: {e})"
        print(
            f"  {tk:14} status={m.get('status','?'):8} mark={mark:>9.4f} ref={ref:>9.4f} "
            f"basis={basis:+7.2f}bp  oi={m.get('open_interest','?')!s:>11}  funding_est={est}"
        )
    return 0


def _cmd_funding(args: argparse.Namespace) -> int:
    from .config import FundingModelConfig
    from .data.kalshi import KalshiPerpClient, funding_history_to_schema

    client = KalshiPerpClient(demo=args.demo)
    now = int(time.time())
    recs = client.funding_history(args.symbol, start_ts=now - args.days * 86400, end_ts=now)
    df = funding_history_to_schema(recs, args.symbol, FundingModelConfig())
    fn = df["funding_now"]
    print(
        f"{args.symbol}: {len(df):,} intervals (~{args.days}d, signed={client.signed})  "
        f"funding/8h: mean={fn.mean()*1e4:+.3f}bp  min={fn.min()*1e4:+.3f}bp  "
        f"max={fn.max()*1e4:+.3f}bp  %positive={(fn>0).mean()*100:.1f}%"
    )
    if args.out:
        df.to_csv(args.out, index=False)
        print(f"funding rows -> {args.out}")
    return 0


def _cmd_poll(args: argparse.Namespace) -> int:
    from .collect import poll_once
    from .config import CollectorConfig, db_path
    from .data import store
    from .data.kalshi import KalshiPerpClient

    cfg = CollectorConfig(depth=args.depth, symbols=tuple(args.symbols) if args.symbols else None)
    client = KalshiPerpClient(demo=args.demo)
    conn = store.connect()
    n_ok, errs = poll_once(client, conn, cfg)
    print(f"poll: {n_ok} ok, {len(errs)} err  -> {db_path()}")
    for e in errs[:5]:
        print(f"  ERR {e}", file=sys.stderr)
    return 0 if not errs else 1


def _cmd_collect(args: argparse.Namespace) -> int:
    from .collect import collect_loop
    from .config import CollectorConfig
    from .data import store
    from .data.kalshi import KalshiPerpClient

    cfg = CollectorConfig(
        interval_secs=args.interval, depth=args.depth,
        symbols=tuple(args.symbols) if args.symbols else None,
    )
    collect_loop(KalshiPerpClient(demo=args.demo), store.connect(), cfg)
    return 0


def _cmd_db_status(_args: argparse.Namespace) -> int:
    import pandas as pd

    from .config import db_path
    from .data import store

    if not db_path().exists():
        print(f"no DB yet at {db_path()} — run `kairos poll` first.")
        return 0
    conn = store.connect()
    cov = store.coverage(conn)
    if cov.empty:
        print(f"DB at {db_path()} is empty — run `kairos poll`.")
        return 0
    cov["first"] = pd.to_datetime(cov["first_poll_ms"], unit="ms", utc=True).dt.strftime("%m-%d %H:%MZ")
    cov["last"] = pd.to_datetime(cov["last_poll_ms"], unit="ms", utc=True).dt.strftime("%m-%d %H:%MZ")
    counts = store.table_counts(conn)
    print(f"DB: {db_path()}")
    print("  rows: " + "  ".join(f"{t}={n}" for t, n in counts.items() if n) + "\n")
    with pd.option_context("display.max_rows", None, "display.width", 200):
        print(cov[["symbol", "snapshots", "settlements", "first", "last"]].to_string(index=False))
    return 0


def _cmd_forward(args: argparse.Namespace) -> int:
    from .config import BacktestConfig, db_path
    from .convergence import forward_edge
    from .data import store

    if not db_path().exists():
        print(f"no DB yet at {db_path()} — run `kairos poll`/`collect` first.")
        return 1
    conn = store.connect()
    df = store.build_labeled_intervals(conn, args.symbol)
    print(f"{args.symbol}: {len(df)} labeled interval(s) with an independent basis.")
    if len(df) < args.min_n:
        print(
            f"Need >= {args.min_n} for a meaningful forward read (I7 wants ~50-100). "
            "Keep the collector running — labels accrue at 3/day per symbol."
        )
        return 0
    fe = forward_edge(df, BacktestConfig())
    print(
        f"\nForwardEdge[{args.symbol}]  n={fe.n}\n"
        f"  nowcast net APR   : {fe.nowcast_net_apr*100:+.2f}%   "
        f"CI [{fe.net_ci_apr[0]*100:+.2f}%, {fe.net_ci_apr[1]*100:+.2f}%]   "
        f"edge_positive={fe.edge_positive}\n"
        f"  raw-carry net APR : {fe.carry_net_apr*100:+.2f}%\n"
        f"  sign agreement    : {fe.sign_agreement:.3f}   "
        f"convergence ratio : {fe.convergence_ratio:.3f}\n"
        "  (edge_positive=True => nowcast net carry is distinguishable from zero — the I7 read.)"
    )
    return 0


def _cmd_dispersion(args: argparse.Namespace) -> int:
    import pandas as pd

    from .crossvenue import collect_live, dispersion, hist_means
    from .data import store
    from .data.kalshi import KalshiPerpClient

    conn = store.connect()
    if args.refresh:
        n = collect_live(conn, KalshiPerpClient(demo=args.demo), assets=[args.asset])
        print(f"refreshed {n} cross-venue funding rows for {args.asset}")
    df = dispersion(conn, args.asset)
    if df.empty:
        print(f"no cross-venue funding for {args.asset} yet — run with --refresh.")
        return 0
    print(f"\n{args.asset}: funding = baseline (interest) + premium (demand).  "
          "premium_apr is the edge; basis_bps is the instantaneous cross-check.\n")
    with pd.option_context("display.width", 200):
        print(df.to_string(index=False))
    fund, prem = df["funding_apr_%"].dropna(), df["premium_apr_%"].dropna()
    if len(fund) >= 2:
        print(f"\n  raw funding dispersion (what you collect): {fund.max()-fund.min():+.2f}%  "
              "<- includes mechanical baselines")
    if len(prem) >= 2:
        print(f"  PREMIUM dispersion (funding minus each venue's baseline — the honest RV): "
              f"{prem.max()-prem.min():+.2f}%")
        print("  (baseline assumed ~11%/yr where not live-sourced — OKX is live; see SOURCES.md.)")
    hm = hist_means(conn, args.asset)
    if not hm.empty:
        print("\n  history (the sound view — mean=baseline, std=premium/demand variation):\n")
        with pd.option_context("display.width", 200):
            print(hm.to_string(index=False))
    return 0


def _cmd_backfill(args: argparse.Namespace) -> int:
    from .crossvenue import backfill_hist
    from .data import store

    conn = store.connect()
    assets = [args.asset] if args.asset else None
    counts = backfill_hist(conn, assets=assets)
    print(f"offshore funding history backfilled -> venue_funding_hist: {counts}")
    print("  (offshore venues keep months/years -> cross-venue carry/RV is backtestable NOW.)")
    return 0


def _cmd_stream(args: argparse.Namespace) -> int:
    from .data.kalshi_ws import stream

    tickers = args.symbols or ["KXBTCPERP", "KXETHPERP", "KXSOLPERP", "KXXRPPERP"]
    stream(tickers, demo=args.demo, max_seconds=args.seconds)
    return 0


def _cmd_digest(_args: argparse.Namespace) -> int:
    from .data import store
    from .report import digest_text

    print(digest_text(store.connect()))
    return 0


def _cmd_index_offset(args: argparse.Namespace) -> int:
    from .crossvenue import index_offset_daily
    from .data import store

    df = index_offset_daily(store.connect(), args.asset, days=args.days)
    if df.empty or len(df) < 2:
        print(f"not enough cross-venue basis history for {args.asset} yet.")
        return 0
    print(f"\n{args.asset} — daily Kalshi-minus-offshore basis offset "
          "(structural index gap vs lag/noise):\n")
    print(df.to_string(index=False))
    off = df["offset_bps"]
    # Stable sign + small std relative to the mean => a persistent (structural) index offset;
    # a large std (mean-reverting around ~0) => just lag/measurement noise, no durable carry.
    structural = off.std() < abs(off.mean()) * 0.5 and abs(off.mean()) > 1.0
    print(f"\n  offset: mean {off.mean():+.1f}bp  std {off.std():.1f}bp  latest {off.iloc[-1]:+.1f}bp  "
          f"-> {'STABLE / structural (small real carry may survive)' if structural else 'NOISY / mean-reverting (no durable edge)'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="kairos", description="Perp funding/basis RV research harness")
    p.add_argument("--demo", action="store_true", help="use Kalshi demo host + demo creds")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("backtest", help="basis nowcast vs no_change/raw_carry baselines")
    b.add_argument("--real", action="store_true", help="use real Kalshi funding history (read-only live)")
    b.add_argument("--symbol", default="KXBTCPERP", help="perp ticker for --real")
    b.add_argument("--days", type=int, default=60, help="history window for --real")
    b.add_argument("--n", type=int, default=3000, help="synthetic intervals (demo only)")
    b.add_argument("--seed", type=int, default=7, help="synthetic seed (demo only)")
    b.add_argument("--promo", action="store_true", help="$0 fees (today's Kalshi launch promo)")
    b.add_argument("--out", default=None, help="write scored rows to CSV")
    b.set_defaults(func=_cmd_backtest)

    m = sub.add_parser("markets", help="live read-only: list perps + funding estimates")
    m.add_argument("--limit", type=int, default=20, help="max markets to print")
    m.set_defaults(func=_cmd_markets)

    f = sub.add_parser("funding", help="pull real funding history (read-only) -> summary/CSV")
    f.add_argument("--symbol", default="KXBTCPERP")
    f.add_argument("--days", type=int, default=30)
    f.add_argument("--out", default=None)
    f.set_defaults(func=_cmd_funding)

    po = sub.add_parser("poll", help="one read-only capture round -> SQLite (cron unit)")
    po.add_argument("--depth", type=int, default=10, help="orderbook levels to store per side")
    po.add_argument("--symbols", nargs="*", default=None, help="tickers (default: all active)")
    po.set_defaults(func=_cmd_poll)

    co = sub.add_parser("collect", help="run the capture loop until interrupted (nohup/systemd unit)")
    co.add_argument("--interval", type=int, default=60, help="seconds between rounds")
    co.add_argument("--depth", type=int, default=10)
    co.add_argument("--symbols", nargs="*", default=None)
    co.set_defaults(func=_cmd_collect)

    ds = sub.add_parser("db-status", help="coverage report for the capture DB")
    ds.set_defaults(func=_cmd_db_status)

    fw = sub.add_parser("forward", help="build labeled intervals from the DB and score forward edge")
    fw.add_argument("--symbol", default="KXBTCPERP")
    fw.add_argument("--min-n", type=int, default=10, help="min labeled intervals before a verdict")
    fw.set_defaults(func=_cmd_forward)

    di = sub.add_parser("dispersion", help="cross-venue annualized funding spread (Kalshi vs offshore)")
    di.add_argument("--asset", default="BTC", help="BTC/ETH/SOL/XRP/DOGE/LTC/LINK/BCH")
    di.add_argument("--refresh", action="store_true", help="pull a fresh live cross-venue round first")
    di.set_defaults(func=_cmd_dispersion)

    bf = sub.add_parser("backfill-hist", help="backfill offshore funding history (read-only) -> DB")
    bf.add_argument("--asset", default=None, help="one asset (default: all mapped)")
    bf.set_defaults(func=_cmd_backfill)

    st = sub.add_parser("stream", help="live read-only Kalshi WS: tick mark/funding + book -> DB")
    st.add_argument("--symbols", nargs="*", default=None, help="perp tickers (default: liquid set)")
    st.add_argument("--seconds", type=float, default=None, help="auto-stop after N seconds")
    st.set_defaults(func=_cmd_stream)

    dg = sub.add_parser("digest", help="print the rich health + signal digest (Slack digest job uses this)")
    dg.set_defaults(func=_cmd_digest)

    io = sub.add_parser("index-offset", help="daily Kalshi-vs-offshore index/basis offset (structural vs noise)")
    io.add_argument("--asset", default="XRP", help="BTC/ETH/SOL/XRP/DOGE/LTC/LINK/BCH")
    io.add_argument("--days", type=int, default=14, help="lookback window in days")
    io.set_defaults(func=_cmd_index_offset)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
