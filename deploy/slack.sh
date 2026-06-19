#!/usr/bin/env bash
# Post a message to Slack via the FORTUNA bot token (chat.postMessage).
# Usage: slack.sh <channel> <text...>   (bot must be invited to the channel)
set -euo pipefail
[ -f /etc/kairos/kairos.env ] && { set -a; . /etc/kairos/kairos.env; set +a; }
chan="${1:?channel}"; shift; text="$*"
[ -z "${KAIROS_SLACK_BOT_TOKEN:-}" ] && { echo "slack: no bot token, skipping" >&2; exit 0; }
payload=$(KCHAN="$chan" KTEXT="$text" python3 - <<'PY'
import json, os
print(json.dumps({"channel": os.environ["KCHAN"], "text": os.environ["KTEXT"]}))
PY
)
resp=$(curl -sS -m 15 -X POST https://slack.com/api/chat.postMessage \
  -H "Authorization: Bearer ${KAIROS_SLACK_BOT_TOKEN}" \
  -H "Content-type: application/json; charset=utf-8" --data "$payload" || echo '{}')
echo "$resp" | grep -q '"ok":true' || echo "slack error: $resp" >&2
