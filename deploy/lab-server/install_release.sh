#!/usr/bin/env bash
# Install one release, switch the gateway to it and prove it healthy - or go back to the release before.
#     sudo bash install_release.sh <tarball> <release-name> [--test]
# The tarball is `git archive` of one commit, made and copied over by deploy.py on the laptop: the server only
# ever runs committed code and never needs access to GitHub. Data and keys live outside every release
# (shared/data, shared/lab.env), so swapping a release never touches them. --test also runs the unit tests here.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
source "$HERE/versions.env"
cd /  # the service user can't read the login user's home, and uv looks around the current folder
source "$HERE/lib.sh"

[[ $EUID -eq 0 ]] || die "run as root: sudo bash $0 <tarball> <release-name>"
TARBALL="${1:-}"; NAME="${2:-}"; TEST="${3:-}"
[[ -f "$TARBALL" && -n "$NAME" ]] || die "usage: sudo bash $0 <tarball> <release-name> [--test]"
[[ "$NAME" =~ ^[A-Za-z0-9._-]+$ ]] || die "bad release name: $NAME"
[[ -x /usr/local/bin/uv ]] || die "the machine isn't prepared yet: run setup.sh first (deploy.py setup)"
[[ -f "$BASE/shared/lab.env" ]] || die "no $BASE/shared/lab.env yet: upload it first (deploy.py env)"
REL="$BASE/releases/$NAME"

echo "== 1/5 unpack $NAME and build its Python environment"
if [[ -f "$REL/.installed" ]]; then
  echo "   already installed"
else
  rm -rf "$REL"
  install -d -o apnasor -g apnasor -m 750 "$REL"
  tar -xzf "$TARBALL" -C "$REL" --no-same-owner
  ln -sfn "$BASE/shared/data" "$REL/data"      # data/ (databases, login-signing key) stays put across releases
  ln -sfn "$BASE/shared/lab.env" "$REL/.env"   # the gateway reads .env from its working directory
  chown -hR apnasor:apnasor "$REL"
  as_app /usr/local/bin/uv venv --quiet --python "$PYTHON_VERSION" "$REL/.venv"
  as_app /usr/local/bin/uv pip install --quiet --compile-bytecode --python "$REL/.venv/bin/python" \
    -r "$REL/deploy/lab-server/requirements.lock"
  as_app "$REL/.venv/bin/python" -m compileall -q "$REL/backend" "$REL/scripts" >/dev/null
  touch "$REL/.installed"
fi

echo "== 2/5 check it starts with this server's settings"
# Imports the whole gateway with lab.env, and refuses anything but testnet: the lab server never holds real money.
(cd "$REL" && as_app .venv/bin/python - <<'EOF'
from backend.gateway.config import settings
import backend.gateway.main  # noqa: F401  (every module imports)
assert settings.exchange_mode == "testnet" and not settings.allow_mainnet, "the lab server runs on testnet only"
keys = [e for e in settings.enabled_exchange_list if settings.exchange_credentials(e)]
print(f"   testnet; exchanges {settings.enabled_exchange_list} (keys for {keys}); "
      f"lab auto-resume {'on' if settings.lab_auto_resume else 'OFF'}")
EOF
)

if [[ "$TEST" == "--test" ]]; then
  echo "== unit tests (a separate copy: no keys, no data)"
  tmp="$(mktemp -d)"
  tar -xzf "$TARBALL" -C "$tmp" --no-same-owner
  chown -R apnasor:apnasor "$tmp"
  (cd "$tmp" && as_app "$REL/.venv/bin/python" -m unittest discover -s backend/tests -t . 2>&1 | tail -n 4)
  rm -rf "$tmp"
fi

echo "== 3/5 back up the databases first"
if systemctl cat apnasor-backup.service &>/dev/null && [[ -e "$BASE/current" ]]; then
  systemctl start apnasor-backup.service && echo "   done: $(ls -1 "$BASE/shared/backups" | tail -n 1)"
else
  echo "   nothing to back up yet (first release)"
fi

echo "== 4/5 switch to $NAME and restart the gateway"
install_units "$REL"
switch_to "$REL"
if restart_gateway; then
  echo "   healthy"
else
  echo "   NOT healthy after 4 minutes - last log lines:"
  journalctl -u apnasor-lab -n 30 --no-pager -o cat | cut -c1-240
  if [[ -L "$BASE/previous" && "$(readlink -e "$BASE/previous")" != "$REL" ]]; then
    prev="$(readlink -e "$BASE/previous")"
    echo "== going back to $(basename "$prev")"
    install_units "$prev"
    switch_to "$prev"
    restart_gateway && echo "   back on $(basename "$prev"), healthy" || echo "   the previous release isn't healthy either"
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
echo "release $NAME is live. The lab bots resume by themselves in about a minute (LAB_AUTO_RESUME)."
