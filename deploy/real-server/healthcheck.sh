#!/usr/bin/env bash
# Every 5 minutes (apnasor-real-health.timer, as root): is the gateway answering? Three failed checks in a row
# (15 minutes) restart it. systemd already restarts a gateway that EXITS; this catches one that still runs but is
# stuck. A gateway someone stopped on purpose (systemctl stop) is left alone. Every order is immediate-or-cancel,
# so a restart never leaves an order resting on an exchange.
set -uo pipefail
FAILS=/run/apnasor-real-health.failures

if curl -fsS --max-time 20 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
  rm -f "$FAILS"
  exit 0
fi
systemctl is-active -q apnasor-real || { rm -f "$FAILS"; exit 0; }  # stopped (or starting): not our business
n=$(( $(cat "$FAILS" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$FAILS"
echo "gateway health check failed ($n in a row)"
if (( n >= 3 )); then
  echo "restarting apnasor-real after $n failed health checks in a row"
  rm -f "$FAILS"
  systemctl restart apnasor-real
fi
