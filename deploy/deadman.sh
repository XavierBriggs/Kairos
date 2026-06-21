#!/usr/bin/env bash
# Dead-man's-switch + AUTO-HEAL. Runs every 5 min via systemd timer, INDEPENDENT of the
# collectors so it fires precisely when they're broken. Row-age catches "alive but stuck"
# (half-open WS) that a process-running check misses. When a unit is stale/down it RESTARTS
# it (via a narrow NOPASSWD sudoers rule, /etc/sudoers.d/kairos-heal) and alerts #kairos-deadman
# with the action taken — so any future stream/collect stall self-recovers in <=5 min.
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
heal() {  # $1=unit  $2=reason — restart via the narrow sudoers rule; record the outcome
  if sudo -n systemctl restart "$1" 2>/dev/null; then
    alerts+=("$2 -> auto-restarted $1")
  else
    alerts+=("$2 -> AUTO-RESTART FAILED for $1 (needs operator)")
  fi
}

# --- stream: stale book (>600s) OR unit down -> heal ---
stream_bad=""
book_ms=$(q "SELECT MAX(recv_ts) FROM ws_book;")
# timestamps are epoch-MILLIS; only judge staleness once data exists (avoid boot false positives)
[ -n "${book_ms:-}" ] && [ "${book_ms:-0}" -gt 0 ] && (( now - book_ms/1000 > 600 )) \
  && stream_bad="stream stale $((now - book_ms/1000))s"
systemctl is-active --quiet kairos-stream || stream_bad="${stream_bad:-stream unit down}"
[ -n "$stream_bad" ] && heal kairos-stream "$stream_bad"

# --- collect: stale poll (>600s) OR unit down -> heal ---
collect_bad=""
poll_ms=$(q "SELECT MAX(run_ts) FROM poll_run;")
[ -n "${poll_ms:-}" ] && [ "${poll_ms:-0}" -gt 0 ] && (( now - poll_ms/1000 > 600 )) \
  && collect_bad="collect stale $((now - poll_ms/1000))s"
systemctl is-active --quiet kairos-collect || collect_bad="${collect_bad:-collect unit down}"
[ -n "$collect_bad" ] && heal kairos-collect "$collect_bad"

# --- disk: alert only (no restart can fix a full disk) ---
disk=$(df --output=pcent /data 2>/dev/null | tail -1 | tr -dc '0-9'); disk=${disk:-0}
(( disk > 85 )) && alerts+=("/data ${disk}% full")

if (( ${#alerts[@]} > 0 )); then
  "$DIR/slack.sh" "$chan" "🔴 KAIROS deadman [$(hostname)]: $(IFS='; '; echo "${alerts[*]}")"
fi
