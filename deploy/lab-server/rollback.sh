#!/usr/bin/env bash
# Go back to the release that ran before the current one:  sudo bash rollback.sh   (deploy.py rollback)
# Only the code changes; the databases and keys (shared/) stay as they are.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/versions.env"
cd /  # the service user can't read the login user's home, and uv looks around the current folder
source "$HERE/lib.sh"

[[ $EUID -eq 0 ]] || die "run as root: sudo bash $0"
prev="$(readlink -f "$BASE/previous" 2>/dev/null || true)"
cur="$(readlink -f "$BASE/current" 2>/dev/null || true)"
[[ -n "$prev" && -f "$prev/.installed" && "$prev" != "$cur" ]] || die "there is no previous release to go back to"

echo "== from $(basename "$cur") back to $(basename "$prev")"
systemctl start apnasor-backup.service || echo "warning: the backup before the switch failed"
install_units "$prev"
switch_to "$prev"   # "previous" now points at the release we left, so a second rollback goes forward again
if restart_gateway; then
  echo "back on $(basename "$prev"), healthy"
else
  journalctl -u apnasor-lab -n 30 --no-pager -o cat | cut -c1-240
  die "$(basename "$prev") is not healthy either - look at the log lines above"
fi
