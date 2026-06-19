#!/usr/bin/env bash
# Health + data summary every 2h -> #kairos-digest. Read-only.
set -uo pipefail
[ -f /etc/kairos/kairos.env ] && { set -a; . /etc/kairos/kairos.env; set +a; }
DB="${KAIROS_DB_PATH:-/data/kairos.db}"
DIR="$(cd "$(dirname "$0")" && pwd)"
chan="${KAIROS_SLACK_CHANNEL_DIGEST:-kairos-digest}"
q() { sqlite3 -noheader -batch "$DB" "$1" 2>/dev/null | head -1; }

counts=$(q "SELECT 'snap='||(SELECT COUNT(*) FROM snapshot)||
  ' trade='||(SELECT COUNT(*) FROM trade)||
  ' vfund='||(SELECT COUNT(*) FROM venue_funding)||
  ' vfhist='||(SELECT COUNT(*) FROM venue_funding_hist)||
  ' wstick='||(SELECT COUNT(*) FROM ws_tick)||
  ' wsbook='||(SELECT COUNT(*) FROM ws_book)||
  ' tick1m='||(SELECT COUNT(*) FROM ws_tick_1m);")
# latest BTC cross-venue funding (annualized %) and premium
btc=$(sqlite3 -noheader -batch "$DB" "SELECT group_concat(venue||':'||ROUND(funding_apr*100,1)||'%',' ')
  FROM venue_funding WHERE asset='BTC' AND poll_ts=(SELECT MAX(poll_ts) FROM venue_funding WHERE asset='BTC');" 2>/dev/null)
# forward-edge progress (labeled intervals toward the I7 gate)
fwd=$(/opt/kairos/venv/bin/kairos forward --symbol KXBTCPERP --min-n 999999 2>/dev/null | head -1)
last=$(q "SELECT datetime(MAX(run_ts)/1000,'unixepoch') FROM poll_run;")
disk=$(df --output=pcent /data 2>/dev/null | tail -1 | tr -d ' ');
up=$(uptime -p 2>/dev/null || true)

msg="📊 KAIROS digest [$(hostname)]
rows: ${counts:-?}
BTC funding (latest): ${btc:-none}
${fwd:-forward: n/a}
last poll: ${last:-never}   /data: ${disk:-?}   ${up:-}"
"$DIR/slack.sh" "$chan" "$msg"
