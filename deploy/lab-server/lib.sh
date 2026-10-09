# Shared by install_release.sh and rollback.sh (sourced, not run). Needs versions.env sourced first.

die() { echo "error: $*" >&2; exit 1; }

# Run a command as the service user, with uv's Python and cache under $BASE.
as_app() {
  sudo -u apnasor -H env UV_PYTHON_INSTALL_DIR="$BASE/python" UV_CACHE_DIR="$BASE/.cache/uv" \
    UV_PYTHON_PREFERENCE=only-managed "$@"
}

# Copy the release's systemd units into place (they travel with the code, so a release can change them).
install_units() {
  local rel="$1"
  install -m 644 "$rel"/deploy/lab-server/systemd/apnasor-*.service "$rel"/deploy/lab-server/systemd/apnasor-*.timer \
    /etc/systemd/system/
  systemctl daemon-reload
}

# Point "current" at a release (atomically) and remember the one before as "previous".
switch_to() {
  local rel="$1" before
  before="$(readlink -e "$BASE/current" 2>/dev/null || true)"  # -e: empty when there is no current yet
  ln -sfn "$rel" "$BASE/current.new"
  mv -Tf "$BASE/current.new" "$BASE/current"
  if [[ -n "$before" && "$before" != "$(readlink -f "$rel")" ]]; then
    ln -sfn "$before" "$BASE/previous.new"
    mv -Tf "$BASE/previous.new" "$BASE/previous"
  fi
}

# The gateway answers /api/health within $1 seconds (start-up trains the research models: ~30-90 s).
wait_healthy() {
  local deadline=$((SECONDS + ${1:-240}))
  while ((SECONDS < deadline)); do
    if curl -fsS --max-time 5 http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
      return 0
    fi
    if ! systemctl is-active -q apnasor-lab; then
      sleep 2  # crashed at start: systemd restarts it in 10 s; keep waiting until the deadline
    fi
    sleep 3
  done
  return 1
}

# Restart the gateway on the current release and prove it healthy. The stop is like Ctrl+C: bots pause and
# (LAB_AUTO_RESUME) resume after the start.
restart_gateway() {
  systemctl enable --quiet apnasor-lab.service apnasor-health.timer apnasor-backup.timer
  systemctl restart apnasor-lab.service
  systemctl start apnasor-health.timer apnasor-backup.timer
  wait_healthy 240
}
