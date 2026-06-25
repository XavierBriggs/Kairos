#!/usr/bin/env bash
# Hourly health + research digest -> #kairos-digest. The content is built by the
# `kairos digest` command (health rate + per-venue health + forward-edge progress +
# cross-venue premium dispersion + Kalshi funding cross-section). Read-only.
set -uo pipefail
[ -f /etc/kairos/kairos.env ] && { set -a; . /etc/kairos/kairos.env; set +a; }
DIR="$(cd "$(dirname "$0")" && pwd)"
chan="${KAIROS_SLACK_CHANNEL_DIGEST:-kairos-digest}"

# 120s cap: even with the recv_ts index a future heavy query must never hang the hourly job.
msg=$(timeout 120 /opt/kairos/venv/bin/kairos digest 2>/dev/null)
[ -z "$msg" ] && msg="📊 KAIROS digest [$(hostname)]: no data yet / digest slow-or-error (check kairos-digest)"
"$DIR/slack.sh" "$chan" "$msg"
