"""Set up, update and look after the lab server from this laptop - over SSH only.

    python deploy/lab-server/deploy.py init --host <server IP> --key <path to the .pem key>
    python deploy/lab-server/deploy.py setup              # once: prepare the machine, keys, history, first release
    python deploy/lab-server/deploy.py status             # what it is doing (daily)
    python deploy/lab-server/deploy.py tunnel             # then: npm --prefix frontend run dev:lab-server
    python deploy/lab-server/deploy.py release [--ref <commit>] [--test]
    python deploy/lab-server/deploy.py env | passwords | check-exchanges | logs | backup-pull | rollback | ssh

The server never pulls from GitHub: each release is `git archive` of ONE commit, copied over SSH, so
uncommitted work on this laptop never reaches it and the server holds no GitHub credentials. Keys reach
the server only through `env`: built from this laptop's .env, testnet keys and login hashes only - a
mainnet key is never copied. Everything local lives in data/lab-server/ (gitignored).
Full guide: project-info/LAB_SERVER_GUIDE.md
"""

import argparse
import getpass
import gzip
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOCAL = ROOT / "data" / "lab-server"  # gitignored (data/)
CONFIG = LOCAL / "server.json"
LOGINS = LOCAL / "logins.env"  # the server's own login password hashes (deploy.py passwords)
BASE = "/opt/apnasor"
UPLOAD = "/tmp/apnasor-upload"  # the login user's own folder (700): uploads pass through it for a moment
KIT = f"{UPLOAD}/kit"
TUNNEL_PORT = 18000  # frontend/.env.lab-server points the lab-server page here

VENUE_KEYS = {
    "binance": ["BINANCE_TESTNET_API_KEY", "BINANCE_TESTNET_API_SECRET"],
    "bybit": ["BYBIT_TESTNET_API_KEY", "BYBIT_TESTNET_API_SECRET"],
    "okx": ["OKX_TESTNET_API_KEY", "OKX_TESTNET_API_SECRET", "OKX_TESTNET_API_PASSPHRASE"],
    "gate": ["GATE_TESTNET_API_KEY", "GATE_TESTNET_API_SECRET"],
}
LOGIN_KEYS = ["AUTH_PASSWORD_HASH_ADMIN", "AUTH_PASSWORD_HASH_TRADER1", "AUTH_PASSWORD_HASH_RISK_MGR"]
COPIED_IF_SET = ["SYMBOLS", "ARB_TAKER_FEES_BPS"]  # same trading universe and fee table as the laptop
SERVER_SETTINGS = {
    "EXCHANGE_MODE": "testnet",  # the lab server never holds real money (install_release.sh refuses anything else)
    "ALLOW_MAINNET": "false",
    "MARKET_DATA_MODE": "mainnet",
    "DEBUG": "false",  # JSON log lines in the journal
    "ARB_ENABLED": "false",  # the lab doesn't need the arbitrage detector (it saves CPU)
    "RISK_POOL_WORKERS": "1",
    "LLM_PROVIDER": "off",  # no copilot on the server: no LLM key there
    "RAFT_ENABLED": "false",
    "LAB_AUTO_RESUME": "true",  # after a crash / reboot / new release the lab bots carry on by themselves
    "LAB_AUTO_RESUME_DELAY_S": "60",
}


# ── helpers ──────────────────────────────────────────────────────────
def fail(message: str) -> None:
    sys.exit(f"error: {message}")


def load_config() -> dict:
    if not CONFIG.exists():
        fail("no server yet - run first:  python deploy/lab-server/deploy.py init --host <IP> --key <key.pem>")
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def ssh_cmd(cfg: dict, *options: str, tty: bool = False) -> list[str]:
    cmd = ["ssh", "-i", cfg["key"], "-p", str(cfg["port"]), "-o", "StrictHostKeyChecking=accept-new",
           "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=20"]
    if tty:
        cmd.append("-t")
    return cmd + list(options) + [f"{cfg['user']}@{cfg['host']}"]


def remote(cfg: dict, script: str, capture: bool = False, check: bool = True) -> str:
    """Run a bash script on the server. It travels on stdin, so nothing needs quoting for Windows."""
    proc = subprocess.run(ssh_cmd(cfg) + ["bash", "-s"], input=("set -euo pipefail\n" + script).encode(),
                          stdout=subprocess.PIPE if capture else None)
    if check and proc.returncode != 0:
        fail(f"the step on the server failed (exit code {proc.returncode}) - see the lines above")
    return proc.stdout.decode(errors="replace") if capture else ""


def upload(cfg: dict, local: Path, remote_name: str) -> str:
    """Copy one file into the server's upload folder; returns its path there."""
    remote(cfg, f"install -d -m 700 {UPLOAD}")
    target = f"{UPLOAD}/{remote_name}"
    cmd = ["scp", "-q", "-i", cfg["key"], "-P", str(cfg["port"]), "-o", "StrictHostKeyChecking=accept-new",
           local.name, f"{cfg['user']}@{cfg['host']}:{target}"]
    if subprocess.run(cmd, cwd=local.parent).returncode != 0:  # a bare file name: no Windows drive-letter trouble
        fail(f"could not copy {local.name} to the server")
    return target


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        fail(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def env_value(value: str) -> str:
    """Single quotes: the .env reader takes the value literally (no $ expansion, no escapes)."""
    if "'" in value or "\n" in value:
        fail("a value in .env contains a quote or a line break - fix it there first")
    return f"'{value}'"


# ── building blocks ──────────────────────────────────────────────────
def make_release(ref: str) -> tuple[str, Path]:
    """`git archive` of one commit. Returns (release name, tarball)."""
    sha = git("rev-parse", "--short=10", f"{ref}^{{commit}}")
    if subprocess.run(["git", "cat-file", "-e", f"{sha}:deploy/lab-server/install_release.sh"], cwd=ROOT,
                      capture_output=True).returncode != 0:
        fail(f"commit {sha} is older than the lab-server kit - use a newer one")
    if git("status", "--porcelain", "--untracked-files=no"):
        print(f"note: this laptop has uncommitted changes - they are NOT in the release (only commit {sha})")
    if not git("branch", "-r", "--contains", sha):
        print(f"note: commit {sha} is not on GitHub yet - push it, so the team can see what the server runs")
    name = f"{datetime.now(UTC):%Y%m%d-%H%M}-{sha}"
    out = LOCAL / "releases" / f"apnasor-{name}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    git("archive", "--format=tar.gz", "-o", str(out), sha)
    for old in sorted(out.parent.glob("apnasor-*.tar.gz"))[:-5]:  # keep the last 5 on the laptop
        old.unlink()
    print(f"release {name}: commit {sha} ({git('log', '-1', '--format=%s', sha)[:70]})")
    return name, out


def push_kit(cfg: dict, name: str, tarball: Path) -> str:
    """Copy the release over and unpack its deploy/lab-server scripts. Returns the tarball's path there."""
    target = upload(cfg, tarball, tarball.name)
    remote(cfg, f"rm -rf {KIT} && mkdir -p {KIT} && tar -xzf {target} -C {KIT} deploy/lab-server")
    return target


def build_env(exchanges: list[str]) -> tuple[Path, list[str]]:
    """The server's settings file from this laptop's .env. Returns (file, what's in it - names only)."""
    from dotenv import dotenv_values

    laptop = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
    logins = dotenv_values(LOGINS) if LOGINS.exists() else {}
    values = dict(SERVER_SETTINGS, ENABLED_EXCHANGES=",".join(exchanges))
    for exchange in exchanges:
        if exchange not in VENUE_KEYS:
            fail(f"unknown exchange {exchange}; choose from {', '.join(VENUE_KEYS)}")
        missing = [k for k in VENUE_KEYS[exchange] if not (laptop.get(k) or "").strip()]
        if missing:
            fail(f"{exchange}: {', '.join(missing)} not set in this laptop's .env")
        values.update({k: laptop[k].strip() for k in VENUE_KEYS[exchange]})
    for key in LOGIN_KEYS:
        if value := (logins.get(key) or laptop.get(key) or "").strip():
            values[key] = value
    for key in COPIED_IF_SET:
        if value := (laptop.get(key) or "").strip():
            values[key] = value
    assert not any("MAINNET_API" in k for k in values), "a mainnet key must never reach the lab server"

    lines = [f"# ApnaSOR lab server settings - made by deploy/lab-server/deploy.py on {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
             "# from the laptop's .env. Make it again with `deploy.py env`; don't edit it on the server.",
             "# Testnet / demo keys only. Lives in /opt/apnasor/shared/lab.env (owner-only)."]
    lines += [f"{k}={env_value(v)}" for k, v in values.items()]
    out = LOCAL / "lab.env"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    if os.name != "nt":
        out.chmod(0o600)
    summary = [f"exchanges {', '.join(exchanges)} (testnet keys)",
               f"real login passwords for {', '.join(k.split('_')[-1].lower() for k in LOGIN_KEYS if k in values)}"
               if any(k in values for k in LOGIN_KEYS) else
               "NO real login passwords: demo logins (password = user name), reachable only through your SSH "
               "tunnel - set real ones with `deploy.py passwords`"]
    return out, summary


def seed_history(cfg: dict) -> None:
    """Give the server this laptop's real-market candle history (public data), so the research models train at
    the first start instead of waiting for days of candles. Only when the server has none yet."""
    from backend.gateway.config import settings

    source = Path(settings.resolved_market_history_path)
    if not source.exists():
        print("no market history on this laptop - the server collects its own")
        return
    if remote(cfg, f"test -e {BASE}/shared/data/market-history.db && echo yes || echo no", capture=True).strip() == "yes":
        print("the server already has a market history - left as it is")
        return
    copy = LOCAL / "market-history.db"
    src, dst = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True), sqlite3.connect(copy)
    src.backup(dst)  # a consistent copy even while the laptop's gateway writes to it
    src.close()
    dst.close()
    target = upload(cfg, copy, "market-history.db")
    remote(cfg, f"sudo install -o apnasor -g apnasor -m 640 {target} {BASE}/shared/data/market-history.db "
                f"&& rm -f {target}")
    copy.unlink()
    print(f"market history copied ({source.stat().st_size / 1e6:.1f} MB)")


# ── commands ─────────────────────────────────────────────────────────
def cmd_init(args) -> None:
    key = Path(args.key).expanduser().resolve()
    if not key.exists():
        fail(f"no key file at {key}")
    cfg = {"host": args.host, "key": str(key), "user": args.user, "port": args.port}
    probe = subprocess.run(ssh_cmd(cfg) + ["bash", "-s"], capture_output=True,
                           input=b'. /etc/os-release; echo "$PRETTY_NAME, $(uname -m), $(nproc) CPUs, '
                                 b'$(free -m | awk \'/Mem:/{print $2}\') MB memory"')
    if probe.returncode != 0:
        err = probe.stderr.decode(errors="replace")
        print(err.strip()[-600:])
        if "UNPROTECTED PRIVATE KEY" in err or "bad permissions" in err:
            print(f'\nThe key file must be readable by you only. In PowerShell:\n'
                  f'  icacls "{key}" /inheritance:r /grant:r "$($env:USERNAME):R"\nthen run init again.')
        fail("could not log in to the server")
    LOCAL.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print(f"connected: {probe.stdout.decode().strip()}\nsaved in {CONFIG.relative_to(ROOT)} - next: deploy.py setup")


def cmd_setup(args) -> None:
    cfg = load_config()
    name, tarball = make_release(args.ref)
    target = push_kit(cfg, name, tarball)
    print("\n== preparing the machine (a few minutes the first time)")
    remote(cfg, f"sudo bash {KIT}/deploy/lab-server/setup.sh </dev/null")
    print("\n== settings and keys")
    install_env(cfg, args.exchanges, restart=False)
    print("\n== market history")
    seed_history(cfg)
    print("\n== first release")
    remote(cfg, f"sudo bash {KIT}/deploy/lab-server/install_release.sh {target} {name} "
                f"{'--test' if args.test else ''} </dev/null")
    print("\nThe lab server runs. Next: deploy.py check-exchanges, then start the lab (LAB_SERVER_GUIDE.md, part C).")


def install_env(cfg: dict, exchanges: list[str], restart: bool) -> None:
    path, summary = build_env(exchanges)
    for line in summary:
        print(f"  {line}")
    target = upload(cfg, path, "lab.env")
    remote(cfg, f"sudo install -o apnasor -g apnasor -m 600 {target} {BASE}/shared/lab.env && rm -f {target}")
    if restart:
        remote(cfg, "if systemctl is-active -q apnasor-lab; then sudo systemctl restart apnasor-lab; "
                    "echo '  gateway restarted with the new settings (lab bots resume in about a minute)'; fi")


def cmd_env(args) -> None:
    install_env(load_config(), args.exchanges, restart=not args.no_restart)


def cmd_passwords(args) -> None:
    from dotenv import dotenv_values

    from backend.gateway.middleware.auth import USERS
    from backend.gateway.middleware.passwords import hash_password, password_problem

    current = dotenv_values(LOGINS) if LOGINS.exists() else {}
    print("Real login passwords for the LAB SERVER only (the laptop's logins don't change).")
    print("Enter skips a user (keeps the password set before, or the demo login).\n")
    for user in sorted(USERS):
        key = f"AUTH_PASSWORD_HASH_{user.upper()}"
        password = getpass.getpass(f"New password for {user}: ")
        if not password:
            continue
        if problem := password_problem(user, password):
            fail(f"{user}: {problem}")
        if getpass.getpass("Same password again: ") != password:
            fail("the two passwords differ - nothing was saved")
        current[key] = hash_password(password)
    LOGINS.parent.mkdir(parents=True, exist_ok=True)
    LOGINS.write_text("".join(f"{k}={v}\n" for k, v in current.items() if v), encoding="utf-8", newline="\n")
    print(f"\nsaved (hashes only) in {LOGINS.relative_to(ROOT)} - send them to the server with:  deploy.py env")


def cmd_release(args) -> None:
    cfg = load_config()
    name, tarball = make_release(args.ref)
    target = push_kit(cfg, name, tarball)
    remote(cfg, f"sudo bash {KIT}/deploy/lab-server/install_release.sh {target} {name} "
                f"{'--test' if args.test else ''} </dev/null")


def cmd_rollback(args) -> None:
    remote(load_config(), f"sudo bash {BASE}/current/deploy/lab-server/rollback.sh </dev/null")


def cmd_status(args) -> None:
    remote(load_config(), f"bash {BASE}/current/deploy/lab-server/status.sh </dev/null")


def cmd_logs(args) -> None:
    cfg = load_config()
    if args.follow:
        subprocess.call(ssh_cmd(cfg, tty=True) + ["journalctl", "-u", "apnasor-lab", "-f", "-o", "cat"])
    else:
        remote(cfg, f"journalctl -u apnasor-lab -n {int(args.lines)} --no-pager -o cat")


def cmd_check_exchanges(args) -> None:
    remote(load_config(), f"sudo -u apnasor -H bash -c 'cd {BASE}/current && .venv/bin/python "
                          f"scripts/check_exchange_keys.py' </dev/null")


def cmd_tunnel(args) -> None:
    cfg = load_config()
    print(f"Tunnel open: http://127.0.0.1:{args.local_port} on this laptop = the lab server's gateway.\n"
          "In another terminal:  npm --prefix frontend run dev:lab-server   then open http://localhost:5180\n"
          "(the page shows a LAB SERVER badge). Ctrl+C here closes the tunnel; the server keeps running.")
    subprocess.call(ssh_cmd(cfg, "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{args.local_port}:127.0.0.1:8000"))


def cmd_backup_pull(args) -> None:
    cfg = load_config()
    name = remote(cfg, f"sudo systemctl start apnasor-backup.service </dev/null; ls -1 {BASE}/shared/backups | tail -n 1",
                  capture=True).strip().splitlines()[-1]
    dest = LOCAL / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["scp", "-q", "-r", "-i", cfg["key"], "-P", str(cfg["port"]), "-o", "StrictHostKeyChecking=accept-new",
           f"{cfg['user']}@{cfg['host']}:{BASE}/shared/backups/{name}", "."]
    if subprocess.run(cmd, cwd=dest).returncode != 0:
        fail("could not copy the backup")
    for packed in (dest / name).glob("*.gz"):  # unpacked next to it, ready to open
        with gzip.open(packed, "rb") as fin, open(packed.with_suffix(""), "wb") as fout:
            shutil.copyfileobj(fin, fout)
    audit_db = next((dest / name).glob("apnasor-*.db"), None)
    print(f"backup {name} is in {(dest / name).relative_to(ROOT)}")
    if audit_db:
        print(f"read it with:  python scripts/lab_status.py --db {audit_db.relative_to(ROOT)}")


def cmd_ssh(args) -> None:
    subprocess.call(ssh_cmd(load_config(), tty=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up, update and look after the lab server (over SSH).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="remember the server and check the login")
    p.add_argument("--host", required=True, help="the server's public IP address")
    p.add_argument("--key", required=True, help="the .pem key file AWS gave you")
    p.add_argument("--user", default="ubuntu")
    p.add_argument("--port", type=int, default=22)
    exchanges = {"type": lambda s: [e.strip().lower() for e in s.split(",") if e.strip()],
                 "default": ["binance", "bybit"], "help": "exchanges the server trades on (default binance,bybit)"}
    p = sub.add_parser("setup", help="once: prepare the machine, settings, history and the first release")
    p.add_argument("--ref", default="HEAD", help="the commit to run (default: this laptop's HEAD)")
    p.add_argument("--exchanges", **exchanges)
    p.add_argument("--test", action="store_true", help="also run the unit tests on the server")
    p = sub.add_parser("release", help="put another commit on the server (backup first, back again if unhealthy)")
    p.add_argument("--ref", default="HEAD")
    p.add_argument("--test", action="store_true")
    p = sub.add_parser("env", help="send fresh settings and keys (from this laptop's .env) and restart")
    p.add_argument("--exchanges", **exchanges)
    p.add_argument("--no-restart", action="store_true")
    sub.add_parser("passwords", help="set the lab server's own login passwords (then: env)")
    sub.add_parser("rollback", help="go back to the release before")
    sub.add_parser("status", help="what the server and the bots are doing")
    p = sub.add_parser("logs", help="the gateway's log")
    p.add_argument("-n", "--lines", default=100)
    p.add_argument("-f", "--follow", action="store_true")
    sub.add_parser("check-exchanges", help="do the keys work from the server (and are the exchanges reachable)?")
    p = sub.add_parser("tunnel", help="reach the server's gateway from this laptop (for the Bots & Lab page)")
    p.add_argument("--local-port", type=int, default=TUNNEL_PORT)
    sub.add_parser("backup-pull", help="make a fresh backup and copy it to this laptop")
    sub.add_parser("ssh", help="a shell on the server")
    args = parser.parse_args()
    {"init": cmd_init, "setup": cmd_setup, "release": cmd_release, "env": cmd_env, "passwords": cmd_passwords,
     "rollback": cmd_rollback, "status": cmd_status, "logs": cmd_logs, "check-exchanges": cmd_check_exchanges,
     "tunnel": cmd_tunnel, "backup-pull": cmd_backup_pull, "ssh": cmd_ssh}[args.command](args)


if __name__ == "__main__":
    main()
