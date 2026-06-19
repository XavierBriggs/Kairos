#!/usr/bin/env bash
# Venue reachability smoke test — run ON the EC2 instance before trusting the deploy.
# 200 = reachable. 451 = geo-block. 403/503 + HTML challenge = datacenter/CDN block
# (fix by re-rolling the Elastic IP, or run the offshore leg on a clean-ASN VPS).
# Kalshi + Hyperliquid are the A-grade core and should always be 200 from US AWS.
set -u
echo "egress IP: $(curl -sS -m10 https://checkip.amazonaws.com 2>/dev/null || echo '?')"
hit() { printf '%-10s %s\n' "$1" "$(curl -sS -o /dev/null -w '%{http_code}' -m15 "$2" 2>/dev/null || echo ERR)"; }
hit KALSHI  "https://api.elections.kalshi.com/trade-api/v2/exchange/status"
hit OKX     "https://www.okx.com/api/v5/public/time"
hit BITGET  "https://api.bitget.com/api/v2/public/time"
hit GATE    "https://api.gateio.ws/api/v4/spot/currencies/BTC"
printf '%-10s %s\n' HYPERLIQUID "$(curl -sS -o /dev/null -w '%{http_code}' -m15 -X POST \
  https://api.hyperliquid.xyz/info -H 'Content-Type: application/json' -d '{"type":"meta"}' 2>/dev/null || echo ERR)"
echo "(Kalshi/Hyperliquid must be 200; OKX/Bitget/Gate 403/503 => re-roll EIP or use a VPS for those.)"
