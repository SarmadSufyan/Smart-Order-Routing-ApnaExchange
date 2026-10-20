# Shared by the real-money server's scripts (sourced, not run). Needs ../lab-server/versions.env sourced first.
# The machine is prepared like the lab server (deploy/lab-server/setup.sh); everything that differs lives here.

SERVICE=apnasor-real
KEYS="$BASE/shared/keys.env"   # the exchange keys: root-only; the gateway gets them through systemd's EnvironmentFile
LIVE="$BASE/shared/live"       # present = real money is on (deploy.py go-live); absent = rehearsal with sandbox keys

die() { echo "error: $*" >&2; exit 1; }

# Run a command as the service user, with uv's Python and cache under $BASE.
as_app() {
  sudo -u apnasor -H env UV_PYTHON_INSTALL_DIR="$BASE/python" UV_CACHE_DIR="$BASE/.cache/uv" \
    UV_PYTHON_PREFERENCE=only-managed "$@"
}

# Run a command as the service user WITH the exchange keys in its environment - the way the gateway gets them.
# The keys travel in the environment, never on a command line (where `ps` would show them).
as_app_with_keys() {
  (
    set -a
    # shellcheck disable=SC1090
    [[ -f "$KEYS" ]] && . "$KEYS"
    set +a
    exec env HOME="$BASE" setpriv --reuid=apnasor --regid=apnasor --init-groups "$@"
  )
}

stage() { [[ -f "$LIVE" ]] && echo live || echo rehearsal; }

# Copy the release's systemd units into place (they travel with the code, so a release can change them).
install_units() {
  local rel="$1"
  install -m 644 "$rel"/deploy/real-server/systemd/apnasor-real*.service "$rel"/deploy/real-server/systemd/apnasor-real*.timer \
    /etc/systemd/system/
  systemctl daemon-reload
}

# Point "current" at a release (atomically) and remember the one before as "previous".
switch_to() {
  local rel="$1" before
  before="$(readlink -e "$BASE/current" 2>/dev/null || true)"
  ln -sfn "$rel" "$BASE/current.new"
  mv -Tf "$BASE/current.new" "$BASE/current"
  if [[ -n "$before" && "$before" != "$(readlink -f "$rel")" ]]; then
    ln -sfn "$before" "$BASE/previous.new"
    mv -Tf "$BASE/previous.new" "$BASE/previous"
  fi
}

# The gateway answers /api/health within $1 seconds.
wait_healthy() {
  local deadline=$((SECONDS + ${1:-240}))
  while ((SECONDS < deadline)); do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
      return 0
    fi
    systemctl is-active -q "$SERVICE" || sleep 2
    sleep 3
  done
  return 1
}

# Restart the gateway on the current release and prove it healthy. The stop is like Ctrl+C: algos and bots pause,
# and on real money nothing resumes by itself.
restart_gateway() {
  systemctl enable --quiet "$SERVICE.service" apnasor-real-health.timer apnasor-real-backup.timer
  systemctl restart "$SERVICE.service"
  systemctl start apnasor-real-health.timer apnasor-real-backup.timer
  wait_healthy 240
}

# One line in the server's release log (who installed what, when, and why it was allowed).
log_release() {
  printf '%s  %s\n' "$(date -u +%Y-%m-%dT%H:%MZ)" "$*" >> "$BASE/shared/releases.log"
}
