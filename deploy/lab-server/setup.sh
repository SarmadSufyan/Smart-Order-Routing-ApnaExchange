#!/usr/bin/env bash
# One-time preparation of the lab server (Ubuntu 24.04, arm64 or x86_64). deploy.py runs it as root:
#     sudo bash setup.sh
# Safe to run again: every step checks first. It prepares the machine (swap, log cap, service user,
# folders, uv + Python); install_release.sh puts the code in and starts the service.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=versions.env
source "$HERE/versions.env"
cd /  # the service user can't read the login user's home, and uv looks around the current folder

[[ $EUID -eq 0 ]] || { echo "Run as root: sudo bash $0"; exit 1; }
source /etc/os-release
[[ "$ID" == "ubuntu" ]] || echo "warning: made for Ubuntu 24.04; this is $PRETTY_NAME"

echo "== packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q curl ca-certificates sqlite3 >/dev/null
# Ubuntu's unattended-upgrades already installs security fixes daily (and never reboots by itself).
# ...but after updating a library, needrestart restarts every service that uses it - on 2026-10-24 a zlib fix
# restarted both gateways at 06:55 UTC. A restart is chosen by a person instead (an order or an algorithm may be
# running; with real money especially): fixes still install, and deploy.py status shows when a restart is waiting.
mkdir -p /etc/needrestart/conf.d
cat > /etc/needrestart/conf.d/90-apnasor.conf <<'NR'
# ApnaSOR (deploy/lab-server/setup.sh): never restart our services automatically after an update.
$nrconf{override_rc}{qr(^apnasor)} = 0;
NR

echo "== clock"
# Exchanges refuse signed requests from a clock that is off by more than a few seconds.
if [[ "$(timedatectl show -p NTPSynchronized --value 2>/dev/null)" != "yes" ]]; then
  echo "warning: the clock is not synchronized yet (timedatectl) - check again in a minute"
fi

echo "== swap (2 GB: a safety margin, not used in normal running)"
if ! swapon --show=NAME --noheadings | grep -qx /swapfile; then
  [[ -f /swapfile ]] || fallocate -l 2G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile >/dev/null
  swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/90-apnasor.conf
sysctl -q -p /etc/sysctl.d/90-apnasor.conf

echo "== log size cap (the journal keeps at most 500 MB)"
mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=500M\n' > /etc/systemd/journald.conf.d/90-apnasor.conf
systemctl restart systemd-journald

echo "== service user and folders under $BASE"
id apnasor &>/dev/null || useradd --system --home-dir "$BASE" --no-create-home --shell /usr/sbin/nologin apnasor
# The login user may read logs and backups (group), never the keys (lab.env is owner-only).
id ubuntu &>/dev/null && usermod -aG apnasor ubuntu
install -d -o apnasor -g apnasor -m 750 "$BASE" "$BASE/releases" "$BASE/shared" "$BASE/shared/data" \
  "$BASE/shared/backups" "$BASE/.cache"

echo "== uv $UV_VERSION"
if [[ "$(/usr/local/bin/uv --version 2>/dev/null | awk '{print $2}')" != "$UV_VERSION" ]]; then
  arch="$(uname -m)"  # aarch64 (Graviton) or x86_64
  name="uv-${arch}-unknown-linux-gnu"
  url="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${name}.tar.gz"
  tmp="$(mktemp -d)"
  curl -fsSL -o "$tmp/$name.tar.gz" "$url"
  curl -fsSL -o "$tmp/$name.tar.gz.sha256" "$url.sha256"
  (cd "$tmp" && sha256sum -c "$name.tar.gz.sha256" >/dev/null)  # refuse a download that doesn't match
  tar -xzf "$tmp/$name.tar.gz" -C "$tmp"
  install -m 755 "$tmp/$name/uv" "$tmp/$name/uvx" /usr/local/bin/
  rm -rf "$tmp"
fi
/usr/local/bin/uv --version

echo "== Python $PYTHON_VERSION (managed by uv, under $BASE/python)"
sudo -u apnasor -H env UV_PYTHON_INSTALL_DIR="$BASE/python" UV_CACHE_DIR="$BASE/.cache/uv" \
  /usr/local/bin/uv python install "$PYTHON_VERSION" --quiet
sudo -u apnasor -H env UV_PYTHON_INSTALL_DIR="$BASE/python" /usr/local/bin/uv python find "$PYTHON_VERSION"

echo "setup done - next: upload lab.env (deploy.py env), then a release (deploy.py release)"
