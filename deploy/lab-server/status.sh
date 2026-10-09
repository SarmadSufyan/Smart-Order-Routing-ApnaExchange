#!/usr/bin/env bash
# The lab server on one screen (deploy.py status runs it as the login user). Read-only: changes nothing.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/versions.env"

name() { basename "$(readlink -f "$1" 2>/dev/null)" 2>/dev/null || echo none; }
echo "== release $(name "$BASE/current")   (previous: $( [[ -L $BASE/previous ]] && name "$BASE/previous" || echo none))"
echo "== gateway: $(systemctl is-active apnasor-lab) since $(systemctl show -p ActiveEnterTimestamp --value apnasor-lab)," \
     "starts at boot: $(systemctl is-enabled apnasor-lab 2>/dev/null)," \
     "automatic restarts since the machine started: $(systemctl show -p NRestarts --value apnasor-lab)"
echo "== machine: up $(uptime -p | sed 's/^up //'), load $(cut -d' ' -f1-3 /proc/loadavg)," \
     "clock synchronized: $(timedatectl show -p NTPSynchronized --value 2>/dev/null)"
free -m | awk '/^Mem:/ {printf "   memory %d of %d MB used", $3, $2} /^Swap:/ {printf ", swap %d MB used\n", $3}'
df -h / | awk 'NR==2 {printf "   disk %s of %s used (%s)\n", $3, $2, $5}'

echo "== exchanges, as the gateway sees them"
if health="$(curl -fsS --max-time 10 http://127.0.0.1:8000/api/health)"; then
  python3 - "$health" <<'EOF'
import json, sys
h = json.loads(sys.argv[1])
print(f"   mode {h['exchange_mode']}, audit database {h['services']['audit_store']}")
for venue, e in sorted(h["exchanges"].items()):
    print(f"   {venue} {e['name']:8} live feed {'connected' if e['feed_connected'] else 'NOT CONNECTED':13} "
          f"books {e['books_synced']:5} trading {'on' if e['trading_enabled'] else 'off (no key)'}")
EOF
else
  echo "   the gateway is not answering (starting up, stopped, or stuck - see deploy.py logs)"
fi

echo "== backups: newest $(ls -1 "$BASE/shared/backups" 2>/dev/null | tail -n 1), $(ls -1 "$BASE/shared/backups" 2>/dev/null | wc -l) kept;" \
     "next $(systemctl show -p NextElapseUSecRealtime --value apnasor-backup.timer 2>/dev/null)"

echo "== bots and lab"
sudo -u apnasor -H bash -c "cd '$BASE/current' && .venv/bin/python scripts/lab_status.py" </dev/null | sed 's/^/   /'

echo "== warnings and errors in the gateway's log, last 24 h (newest 10)"
journalctl -u apnasor-lab --since "-24h" --no-pager -o cat 2>/dev/null \
  | grep -E '"level": "(warning|error|critical)"' | tail -n 10 | cut -c1-230 | sed 's/^/   /'
echo "   ($(journalctl -u apnasor-lab --since '-24h' --no-pager -o cat 2>/dev/null | grep -cE '"level": "(error|critical)"') errors in 24 h)"
