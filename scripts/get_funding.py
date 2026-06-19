#!/usr/bin/env python3
"""Pull real Kalshi perp funding history (read-only) -> data/kairos/<symbol>.csv.

Usage:  python scripts/get_funding.py [--days 90] [--demo] KXBTCPERP KXETHPERP

Read-only: uses only GET /margin/funding_rates/historical via the signed client.
Writes the canonical leakage-safe schema the backtest consumes.
"""
from __future__ import annotations

import argparse
import time

from kairos.config import FundingModelConfig, data_dir
from kairos.data.kalshi import KalshiPerpClient, funding_history_to_schema


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("symbols", nargs="*", default=["KXBTCPERP", "KXETHPERP"])
    ap.add_argument("--days", type=int, default=90)
    ap.add_argument("--demo", action="store_true")
    args = ap.parse_args()

    out_dir = data_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    client = KalshiPerpClient(demo=args.demo)
    cfg = FundingModelConfig()
    now = int(time.time())
    for sym in args.symbols or ["KXBTCPERP", "KXETHPERP"]:
        recs = client.funding_history(sym, start_ts=now - args.days * 86400, end_ts=now)
        df = funding_history_to_schema(recs, sym, cfg)
        path = out_dir / f"{sym}.csv"
        df.to_csv(path, index=False)
        print(f"{sym}: {len(df):,} intervals (signed={client.signed}) -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
