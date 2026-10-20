#!/usr/bin/env bash
# The real-money server on one screen (deploy.py status runs it as the login user). Read-only: changes nothing.
# It never prints a key: keys.env shows names and the last 4 characters only.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lab-server/versions.env"
source "$HERE/lib.sh"

name() { basename "$(readlink -f "$1" 2>/dev/null)" 2>/dev/null || echo none; }
if [[ -f "$LIVE" ]]; then
  echo "== stage: LIVE - REAL MONEY (since $(date -u -r "$LIVE" +%Y-%m-%d\ %H:%MZ))"
else
  echo "== stage: REHEARSAL - sandbox keys, no real money"
fi
echo "== release $(name "$BASE/current")   (previous: $( [[ -L $BASE/previous ]] && name "$BASE/previous" || echo none))"
echo "== gateway: $(systemctl is-active "$SERVICE") since $(systemctl show -p ActiveEnterTimestamp --value "$SERVICE")," \
     "starts at boot: $(systemctl is-enabled "$SERVICE" 2>/dev/null)," \
     "automatic restarts since the machine started: $(systemctl show -p NRestarts --value "$SERVICE")"
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
          f"books {e['books_synced']:5} trading {'on' if e['trading_enabled'] else 'OFF'}")
EOF
else
  echo "   the gateway is not answering (starting up, stopped, or stuck - see deploy.py logs)"
fi

echo "== keys on the server (names only)"
sudo awk '/^[A-Z_]+=/{n = index($0, "="); v = substr($0, n + 1); gsub(/'"'"'/, "", v);
                       printf "   %s ...%s\n", substr($0, 1, n - 1), substr(v, length(v) - 3)}' "$KEYS" \
  2>/dev/null || echo "   none yet (deploy.py keys)"

echo "== what the exchanges said about the keys and fees (gateway log, last 24 h, newest per exchange)"
journalctl -u "$SERVICE" --since "-24h" --no-pager -o cat 2>/dev/null \
  | grep -E '"event": "(key_confirmed_trade_only|key_refused_for_real_money|account_fee_read|key_permissions_check_failed)"' \
  | python3 -c '
import json, sys
last = {}
for line in sys.stdin:
    try:
        e = json.loads(line)
    except ValueError:
        continue
    last[(e.get("venue_id"), e["event"].startswith("account_fee"))] = e
for (venue, _), e in sorted(last.items(), key=lambda x: str(x[0])):
    fee = e.get("taker_bps")
    detail = e.get("reason") or e.get("summary") or ("taker %s bps" % fee if fee is not None else e.get("error", ""))
    print("   %s %s: %s" % (venue, e.get("event"), str(detail)[:150]))
if not last:
    print("   nothing yet (these checks run on real money only)")
'

echo "== backups: newest $(ls -1 "$BASE/shared/backups" 2>/dev/null | tail -n 1), $(ls -1 "$BASE/shared/backups" 2>/dev/null | wc -l) kept;" \
     "next $(systemctl show -p NextElapseUSecRealtime --value apnasor-real-backup.timer 2>/dev/null)"
echo "== release log (newest 5)"
tail -n 5 "$BASE/shared/releases.log" 2>/dev/null | sed 's/^/   /' || echo "   empty"

echo "== warnings and errors in the gateway's log, last 24 h (newest 10)"
journalctl -u "$SERVICE" --since "-24h" --no-pager -o cat 2>/dev/null \
  | grep -E '"level": "(warning|error|critical)"' | tail -n 10 | cut -c1-230 | sed 's/^/   /'
echo "   ($(journalctl -u "$SERVICE" --since '-24h' --no-pager -o cat 2>/dev/null | grep -cE '"level": "(error|critical)"') errors in 24 h)"
