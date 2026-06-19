#!/usr/bin/env bash
# Nightly retention. Philosophy: the raw L2 book is IRREPLACEABLE (Kalshi perps are new,
# no vendor sells history), so keep ALL of it until disk pressure, then drop only the
# oldest. The 1-minute ws_tick rollup is kept INDEFINITELY as the compact archive.
set -uo pipefail
[ -f /etc/kairos/kairos.env ] && { set -a; . /etc/kairos/kairos.env; set +a; }
DB="${KAIROS_DB_PATH:-/data/kairos.db}"
HIGH="${KAIROS_DISK_HIGH:-75}"        # start pruning ws_book above this %
RAW_TICK_DAYS="${KAIROS_RAW_TICK_DAYS:-30}"
usage() { df --output=pcent /data 2>/dev/null | tail -1 | tr -dc '0-9'; }

# 1) roll up completed minutes of ws_tick -> ws_tick_1m (idempotent; kept forever)
sqlite3 "$DB" <<'SQL'
INSERT OR IGNORE INTO ws_tick_1m
  (symbol, minute_ts, n, px_open, px_high, px_low, px_close, bid_mean, ask_mean, spread_bps_mean, imbalance_mean)
SELECT t.symbol, (t.recv_ts/60000)*60 AS minute_ts, COUNT(*),
  (SELECT price FROM ws_tick a WHERE a.symbol=t.symbol AND a.recv_ts/60000=t.recv_ts/60000 ORDER BY a.recv_ts ASC  LIMIT 1),
  MAX(t.price), MIN(t.price),
  (SELECT price FROM ws_tick a WHERE a.symbol=t.symbol AND a.recv_ts/60000=t.recv_ts/60000 ORDER BY a.recv_ts DESC LIMIT 1),
  AVG(t.bid), AVG(t.ask),
  AVG(CASE WHEN t.bid>0 AND t.ask>0 THEN (t.ask-t.bid)/((t.ask+t.bid)/2.0)*1e4 END),
  AVG(CASE WHEN (t.bid_size+t.ask_size)>0 THEN t.bid_size/(t.bid_size+t.ask_size) END)
FROM ws_tick t
WHERE (t.recv_ts/60000)*60 < (CAST(strftime('%s','now') AS INT)/60)*60   -- completed minutes only
GROUP BY t.symbol, t.recv_ts/60000;
SQL

# 2) disk-pressure prune of raw ws_book (keep max raw that fits), oldest-first, batched
i=0
while [ "$(usage)" -gt "$HIGH" ] && [ "$i" -lt 200 ]; do
  ch=$(sqlite3 "$DB" "DELETE FROM ws_book WHERE rowid IN (SELECT rowid FROM ws_book ORDER BY recv_ts ASC LIMIT 200000); SELECT changes();" 2>/dev/null | tail -1)
  [ "${ch:-0}" -eq 0 ] && break
  i=$((i+1))
done

# 3) prune raw ws_tick older than RAW_TICK_DAYS (already rolled up)
sqlite3 "$DB" "DELETE FROM ws_tick WHERE recv_ts < (CAST(strftime('%s','now') AS INT) - ${RAW_TICK_DAYS}*86400)*1000;" 2>/dev/null || true

# 4) reclaim freed pages + truncate the WAL
sqlite3 "$DB" "PRAGMA incremental_vacuum; PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
echo "prune done: /data at $(usage)%"
