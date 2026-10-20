#!/usr/bin/env bash
# Install one release on the REAL-MONEY server, check it is safe for this server's stage, switch the gateway to it
# and prove it healthy - or go back to the release before.
#     sudo bash install_release.sh <tarball> <release-name> [--test]
# deploy.py decides whether the release may come here at all (the promotion gate: the same commit ran >= 3 days
# on the lab server with CI green, or a written reason) and records that in shared/releases.log.
# Data and keys live outside every release (shared/data, shared/real.env, shared/keys.env).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/../lab-server/versions.env"
cd /  # the service user can't read the login user's home, and uv looks around the current folder
source "$HERE/lib.sh"

[[ $EUID -eq 0 ]] || die "run as root: sudo bash $0 <tarball> <release-name>"
TARBALL="${1:-}"; NAME="${2:-}"; TEST="${3:-}"
[[ -f "$TARBALL" && -n "$NAME" ]] || die "usage: sudo bash $0 <tarball> <release-name> [--test]"
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad release name: $NAME"
[[ -x /usr/local/bin/uv ]] || die "the machine isn't prepared yet: deploy.py setup"
[[ -f "$BASE/shared/real.env" ]] || die "no $BASE/shared/real.env yet: deploy.py env"
REL="$BASE/releases/$NAME"

echo "== 1/5 unpack $NAME and build its Python environment"
if [[ -f "$REL/.installed" ]]; then
  echo "   already installed"
else
  rm -rf "$REL"
  install -d -o apnasor -g apnasor -m 750 "$REL"
  tar -xzf "$TARBALL" -C "$REL" --no-same-owner
  ln -sfn "$BASE/shared/data" "$REL/data"       # databases and the login-signing key stay put across releases
  ln -sfn "$BASE/shared/real.env" "$REL/.env"   # settings (no exchange keys: those come from keys.env)
  chown -hR apnasor:apnasor "$REL"
  as_app /usr/local/bin/uv venv --quiet --python "$PYTHON_VERSION" "$REL/.venv"
  as_app /usr/local/bin/uv pip install --quiet --compile-bytecode --python "$REL/.venv/bin/python" \
    -r "$REL/deploy/lab-server/requirements.lock"
  as_app "$REL/.venv/bin/python" -m compileall -q "$REL/backend" "$REL/scripts" >/dev/null
  touch "$REL/.installed"
fi

echo "== 2/5 is it safe for this server's stage ($(stage))?"
bash "$REL/deploy/real-server/check_settings.sh" "$REL"

if [[ "$TEST" == "--test" ]]; then
  echo "== unit tests (a separate copy: no keys, no data)"
  tmp="$(mktemp -d)"
  tar -xzf "$TARBALL" -C "$tmp" --no-same-owner
  chown -R apnasor:apnasor "$tmp"
  # The names of failing tests, not only the count.
  (cd "$tmp" && as_app "$REL/.venv/bin/python" -m unittest discover -s backend/tests -t . 2>&1 \
     | grep -E "^(FAIL|ERROR): |^Ran |^OK|^FAILED")
  rm -rf "$tmp"
fi

echo "== 3/5 back up the databases first"
if systemctl cat apnasor-real-backup.service &>/dev/null && [[ -e "$BASE/current" ]]; then
  systemctl start apnasor-real-backup.service && echo "   done: $(ls -1 "$BASE/shared/backups" | tail -n 1)"
else
  echo "   nothing to back up yet (first release)"
fi

echo "== 4/5 switch to $NAME and restart the gateway"
install_units "$REL"
switch_to "$REL"
if restart_gateway; then
  echo "   healthy"
  log_release "installed $NAME ($(stage))"
else
  echo "   NOT healthy after 4 minutes - last log lines:"
  journalctl -u "$SERVICE" -n 30 --no-pager -o cat | cut -c1-240
  if [[ -L "$BASE/previous" && "$(readlink -e "$BASE/previous")" != "$REL" ]]; then
    prev="$(readlink -e "$BASE/previous")"
    echo "== going back to $(basename "$prev")"
    install_units "$prev"
    switch_to "$prev"
    restart_gateway && echo "   back on $(basename "$prev"), healthy" || echo "   the previous release isn't healthy either"
    log_release "FAILED $NAME - back on $(basename "$prev")"
  fi
  die "release $NAME was not installed"
fi

echo "== 5/5 tidy up: keep the 3 newest releases (and the current and previous ones)"
keep_cur="$(readlink -e "$BASE/current")"; keep_prev="$(readlink -e "$BASE/previous" 2>/dev/null || true)"
ls -1dt "$BASE"/releases/*/ 2>/dev/null | tail -n +4 | while read -r old; do
  old="${old%/}"
  [[ "$old" == "$keep_cur" || "$old" == "$keep_prev" ]] && continue
  rm -rf "$old" && echo "   removed $(basename "$old")"
done
rm -f "$TARBALL"
echo "release $NAME is live on the real-money server ($(stage)). Algos and bots that were running stay paused."
