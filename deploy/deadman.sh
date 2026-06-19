#!/usr/bin/env bash
# Dead-man's-switch: alert #kairos-deadman if the collectors go stale, a unit fails,
# or disk fills. Runs every 5 min via systemd timer, INDEPENDENT of the collectors so
# it fires precisely when they're broken. Row-age catches "alive but stuck" (half-open
# WS) that a process-running check misses.
set -uo pipefail
[ -f /etc/kairos/kairos.env ] && { set -a; . /etc/kairos/kairos.env; set +a; }
DB="${KAIROS_DB_PATH:-/data/kairos.db}"
DIR="$(cd "$(dirname "$0")" && pwd)"
chan="${KAIROS_SLACK_CHANNEL_DEADMAN:-kairos-deadman}"
now=$(date +%s)
q() { sqlite3 -cmd ".timeout 8000" -noheader -batch "$DB" "$1" 2>/dev/null | head -1; }

# A collect round makes ~70 HTTP calls + a 60s sleep, so poll_run rows are legitimately
# 2-4 min apart — thresholds must exceed that or they false-alarm on healthy slow rounds.
alerts=()
poll_ms=$(q "SELECT MAX(run_ts) FROM poll_run;")
book_ms=$(q "SELECT MAX(recv_ts) FROM ws_book;")
# timestamps are epoch-MILLIS; only alert once data exists (avoid boot false positives)
[ -n "${poll_ms:-}" ] && [ "${poll_ms:-0}" -gt 0 ] && (( now - poll_ms/1000 > 600 )) \
  && alerts+=("collect stale $((now - poll_ms/1000))s")
[ -n "${book_ms:-}" ] && [ "${book_ms:-0}" -gt 0 ] && (( now - book_ms/1000 > 600 )) \
  && alerts+=("stream stale $((now - book_ms/1000))s")
for u in kairos-collect kairos-stream; do
  systemctl is-active --quiet "$u" || alerts+=("unit $u down")
done
disk=$(df --output=pcent /data 2>/dev/null | tail -1 | tr -dc '0-9'); disk=${disk:-0}
(( disk > 85 )) && alerts+=("/data ${disk}% full")

if (( ${#alerts[@]} > 0 )); then
  "$DIR/slack.sh" "$chan" "🔴 KAIROS deadman [$(hostname)]: $(IFS='; '; echo "${alerts[*]}")"
fi
