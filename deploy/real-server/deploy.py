"""Set up, update and look after the REAL-MONEY server from this laptop - over SSH only (Phase D plan, D1-1).

    python deploy/real-server/deploy.py init --host <server IP> --key <path to the .pem key>
    python deploy/real-server/deploy.py replace-ssh-key       # once: a login key made on this laptop, the old one removed
    python deploy/real-server/deploy.py passwords             # the real-money server's own login passwords
    python deploy/real-server/deploy.py setup [--test]        # once: machine, settings, sandbox keys, first release
    python deploy/real-server/deploy.py status | logs | tunnel | backup-pull | ssh | rollback
    python deploy/real-server/deploy.py keys                  # D3: type the REAL keys (hidden) -> the server only
    python deploy/real-server/deploy.py check-keys            # D3: what the exchanges say about them (read-only)
    python deploy/real-server/deploy.py telegram              # phone alerts and /stop (token hidden -> the server)
    python deploy/real-server/deploy.py ai-key [--remove]     # the copilot's Claude key (hidden -> the server; A5)
    python deploy/real-server/deploy.py go-live               # D4: real money on (asks you to type REAL MONEY)
    python deploy/real-server/deploy.py release [--ref <commit>] [--reason "..."] [--test]

The server starts in REHEARSAL: the sandbox keys of this laptop, EXCHANGE_MODE=testnet, so the whole machine is
tested before any real key exists. `go-live` turns real money on: the sandbox keys are removed, the settings switch
to mainnet with the hard caps, and the safety check (check_settings.sh) must pass.

Real keys never touch this laptop: `keys` asks for them in a hidden prompt and sends them over SSH (on stdin, never
on a command line) straight into the server's root-only keys.env. Settings come from this laptop's .env (caps,
symbols, fee table) and data/real-server/logins.env (password hashes) - never an exchange key.

Once live, `release` takes a commit only if it has run >= 3 days on the lab server and its CI is green (the
promotion gate), or with --reason, which is written into the server's release log.
Everything local lives in data/real-server/ (gitignored). The plan: project-info/PHASE_D_PLAN.md
"""

import argparse
import getpass
import gzip
import json
import os
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

LOCAL = ROOT / "data" / "real-server"  # gitignored (data/)
CONFIG = LOCAL / "server.json"
LOGINS = LOCAL / "logins.env"
LAB_CONFIG = ROOT / "data" / "lab-server" / "server.json"
BASE = "/opt/apnasor"
UPLOAD = "/tmp/apnasor-upload"
KIT = f"{UPLOAD}/kit"
TUNNEL_PORT = 28000  # frontend/.env.real-server points the real-money page here
GATE_HOURS = 72  # a commit runs this long on the lab server before it may reach real money

EXCHANGE_KEYS = {
    "binance": ["API_KEY", "API_SECRET"],
    "bybit": ["API_KEY", "API_SECRET"],
    "okx": ["API_KEY", "API_SECRET", "API_PASSPHRASE"],
    "gate": ["API_KEY", "API_SECRET"],
}
LOGIN_KEYS = ["AUTH_PASSWORD_HASH_ADMIN", "AUTH_PASSWORD_HASH_TRADER1", "AUTH_PASSWORD_HASH_RISK_MGR"]
AI_KEY = "COPILOT_CLAUDE_API_KEY"  # decision A5: the copilot's Claude key lives in keys.env, typed with ai-key
# Copied from this laptop's .env when set: the same universe, fee table and (if you set them) real-money caps.
COPIED_IF_SET = ["SYMBOLS", "ARB_TAKER_FEES_BPS", "MAINNET_MAX_ORDER_USDT", "MAINNET_MAX_ALGO_USDT",
                 "MAINNET_MAX_EXPOSURE_USDT", "MAINNET_MAX_TRADING_LOSS_USDT"]
COMMON_SETTINGS = {
    "MARKET_DATA_MODE": "mainnet",
    "DEBUG": "false",
    "ARB_ENABLED": "false",  # the detector isn't needed here (it saves CPU)
    "RISK_POOL_WORKERS": "1",
    # Decision A5 (2026-10-23): the copilot's AI on real money is Claude (its API data isn't used for training). It
    # stays off until a key is typed with `deploy.py ai-key` - without one the copilot says so and its quick
    # analysis still works. Never the free Gemini tier here: it may use what it is sent, i.e. real balances.
    "LLM_PROVIDER": "claude",
    "RAFT_ENABLED": "false",
    "LAB_AUTO_RESUME": "false",  # on real money nothing resumes by itself after a restart
    "MAINNET_REQUIRE_IP_LOCK": "true",  # each real key must be locked to this server's IP
    "MAINNET_CYCLE_BOTS": "false",  # decision A6: no bots on real money until the lab's verdict
}
STAGE_SETTINGS = {
    "rehearsal": {"EXCHANGE_MODE": "testnet", "ALLOW_MAINNET": "false"},
    "live": {"EXCHANGE_MODE": "mainnet", "ALLOW_MAINNET": "true"},
}


# ── helpers ──────────────────────────────────────────────────────────
def fail(message: str) -> None:
    sys.exit(f"error: {message}")


def load_config(path: Path = CONFIG) -> dict:
    if not path.exists():
        fail("no server yet - run first:  python deploy/real-server/deploy.py init --host <IP> --key <key.pem>")
    return json.loads(path.read_text(encoding="utf-8"))


def ssh_cmd(cfg: dict, *options: str, tty: bool = False) -> list[str]:
    cmd = ["ssh", "-i", cfg["key"], "-p", str(cfg["port"]), "-o", "StrictHostKeyChecking=accept-new",
           "-o", "ServerAliveInterval=30", "-o", "ConnectTimeout=20", "-o", "IdentitiesOnly=yes"]
    if tty:
        cmd.append("-t")
    return cmd + list(options) + [f"{cfg['user']}@{cfg['host']}"]


def remote(cfg: dict, script: str, capture: bool = False, check: bool = True, stdin: bytes | None = None) -> str:
    """Run a bash script on the server. The script travels on stdin, so nothing needs quoting for Windows.

    With ``stdin``, the script is passed as an argument instead and ``stdin`` goes to it - that is how keys
    travel: in the data stream, never in a command line."""
    if stdin is None:
        proc = subprocess.run(ssh_cmd(cfg) + ["bash", "-s"], input=("set -euo pipefail\n" + script).encode(),
                              stdout=subprocess.PIPE if capture else None)
    else:
        proc = subprocess.run(ssh_cmd(cfg) + ["bash", "-c", shlex.quote("set -euo pipefail\n" + script)],
                              input=stdin, stdout=subprocess.PIPE if capture else None)
    if check and proc.returncode != 0:
        fail(f"the step on the server failed (exit code {proc.returncode}) - see the lines above")
    return proc.stdout.decode(errors="replace") if capture else ""


def upload(cfg: dict, local: Path, remote_name: str) -> str:
    remote(cfg, f"install -d -m 700 {UPLOAD}")
    target = f"{UPLOAD}/{remote_name}"
    cmd = ["scp", "-q", "-i", cfg["key"], "-P", str(cfg["port"]), "-o", "StrictHostKeyChecking=accept-new",
           "-o", "IdentitiesOnly=yes", local.name, f"{cfg['user']}@{cfg['host']}:{target}"]
    if subprocess.run(cmd, cwd=local.parent).returncode != 0:
        fail(f"could not copy {local.name} to the server")
    return target


def git(*args: str) -> str:
    out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if out.returncode != 0:
        fail(f"git {' '.join(args)}: {out.stderr.strip()}")
    return out.stdout.strip()


def env_value(value: str) -> str:
    if "'" in value or "\n" in value:
        fail("a value contains a quote or a line break - fix it first")
    return f"'{value}'"


def remote_ok(cfg: dict, script: str) -> bool:
    """Run a script on the server; True if it succeeded (its output is shown)."""
    return subprocess.run(ssh_cmd(cfg) + ["bash", "-s"], input=("set -euo pipefail\n" + script).encode()).returncode == 0


def server_stage(cfg: dict) -> str:
    out = remote(cfg, f"test -f {BASE}/shared/live && echo live || echo rehearsal", capture=True).strip()
    return "live" if out == "live" else "rehearsal"


# ── building blocks ──────────────────────────────────────────────────
def make_release(ref: str) -> tuple[str, Path, str]:
    """`git archive` of one commit. Returns (release name, tarball, full commit hash)."""
    full = git("rev-parse", f"{ref}^{{commit}}")
    sha = full[:10]
    if subprocess.run(["git", "cat-file", "-e", f"{sha}:deploy/real-server/install_release.sh"], cwd=ROOT,
                      capture_output=True).returncode != 0:
        fail(f"commit {sha} is older than the real-money server kit - use a newer one")
    if git("status", "--porcelain", "--untracked-files=no"):
        print(f"note: this laptop has uncommitted changes - they are NOT in the release (only commit {sha})")
    name = f"{datetime.now(UTC):%Y%m%d-%H%M}-{sha}"
    out = LOCAL / "releases" / f"apnasor-{name}.tar.gz"
    out.parent.mkdir(parents=True, exist_ok=True)
    git("archive", "--format=tar.gz", "-o", str(out), sha)
    for old in sorted(out.parent.glob("apnasor-*.tar.gz"))[:-5]:
        old.unlink()
    print(f"release {name}: commit {sha} ({git('log', '-1', '--format=%s', sha)[:70]})")
    return name, out, full


def push_kit(cfg: dict, tarball: Path) -> str:
    """Copy the release over and unpack the server kits it needs. Returns the tarball's path there."""
    target = upload(cfg, tarball, tarball.name)
    remote(cfg, f"rm -rf {KIT} && mkdir -p {KIT} && tar -xzf {target} -C {KIT} deploy/real-server deploy/lab-server")
    return target


def build_env(stage: str, exchanges: list[str]) -> tuple[Path, list[str]]:
    """The server's settings (real.env): this laptop's caps / symbols / fee table and the login hashes - no keys."""
    from dotenv import dotenv_values

    laptop = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
    logins = dotenv_values(LOGINS) if LOGINS.exists() else {}
    missing = [k.split("_")[-1].lower() for k in LOGIN_KEYS if not (logins.get(k) or "").strip()]
    if missing:
        fail(f"no real-money-server password for {', '.join(missing)} yet - run:  deploy.py passwords")
    for exchange in exchanges:
        if exchange not in EXCHANGE_KEYS:
            fail(f"unknown exchange {exchange}; choose from {', '.join(EXCHANGE_KEYS)}")
    values = {**COMMON_SETTINGS, **STAGE_SETTINGS[stage], "ENABLED_EXCHANGES": ",".join(exchanges)}
    values.update({k: logins[k].strip() for k in LOGIN_KEYS})
    for key in COPIED_IF_SET:
        if value := (laptop.get(key) or "").strip():
            values[key] = value
    assert not any(k.endswith(("_API_KEY", "_API_SECRET", "_API_PASSPHRASE")) for k in values), "keys go to keys.env"

    lines = [f"# ApnaSOR real-money server settings ({stage}) - made by deploy/real-server/deploy.py on "
             f"{datetime.now(UTC):%Y-%m-%d %H:%M} UTC.",
             "# No exchange key here (those are in the root-only keys.env). Make it again with deploy.py env."]
    lines += [f"{k}={env_value(v)}" for k, v in values.items()]
    out = LOCAL / "real.env"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    caps = ", ".join(f"{k.removeprefix('MAINNET_MAX_').lower()} {values[k]}" for k in COPIED_IF_SET[2:] if k in values)
    return out, [f"stage {stage}: EXCHANGE_MODE={values['EXCHANGE_MODE']}, exchanges {', '.join(exchanges)}",
                 f"caps: {caps or 'the defaults (25 / 100 / 150 / 10 USDT)'}; copilot AI Claude (off until "
                 "deploy.py ai-key); cycle bots off",
                 "real login passwords for admin, trader1, risk_mgr"]


def install_env(cfg: dict, stage: str, exchanges: list[str]) -> None:
    path, summary = build_env(stage, exchanges)
    for line in summary:
        print(f"  {line}")
    target = upload(cfg, path, "real.env")
    remote(cfg, f"sudo install -o apnasor -g apnasor -m 600 {target} {BASE}/shared/real.env && rm -f {target}")
    path.unlink()  # the hashes stay in logins.env; no second copy lying around


def send_keys(cfg: dict, lines: list[str], drop: str | None = None) -> None:
    """Merge NAME=value lines into the server's keys.env (root-only), through stdin."""
    script = f"sudo python3 {KIT}/deploy/real-server/write_keys.py {BASE}/shared/keys.env"
    if drop:
        script += f" --drop {drop}"
    remote(cfg, script, stdin=("\n".join(lines) + "\n").encode())


def seed_history(cfg: dict) -> None:
    """This laptop's real-market candle history (public data), so the research models train at the first start."""
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
    src.backup(dst)
    src.close()
    dst.close()
    target = upload(cfg, copy, "market-history.db")
    remote(cfg, f"sudo install -o apnasor -g apnasor -m 640 {target} {BASE}/shared/data/market-history.db "
                f"&& rm -f {target}")
    copy.unlink()
    print(f"market history copied ({source.stat().st_size / 1e6:.1f} MB)")


# ── the promotion gate ───────────────────────────────────────────────
def lab_has_run(full_sha: str, now: float | None = None) -> tuple[bool, str]:
    """Has the lab server been running this commit for >= GATE_HOURS? (its current release, switched to when)"""
    if not LAB_CONFIG.exists():
        return False, "this laptop has no lab server configured (data/lab-server/server.json)"
    lab = load_config(LAB_CONFIG)
    proc = subprocess.run(ssh_cmd(lab) + ["bash", "-s"], capture_output=True, timeout=90,
                          input=f"readlink -f {BASE}/current; stat -c %Y {BASE}/current".encode())
    lines = proc.stdout.decode(errors="replace").split()
    if proc.returncode != 0 or len(lines) < 2:
        return False, "the lab server didn't answer (its SSH rule may need your current IP)"
    return gate_verdict(full_sha, os.path.basename(lines[0]), int(lines[1]), now or time.time())


def gate_verdict(full_sha: str, lab_release: str, switched_at: float, now: float) -> tuple[bool, str]:
    """Pure: the lab release name ends with the commit's 10-character hash; the switch time decides the age."""
    lab_sha = lab_release.rsplit("-", 1)[-1]
    if not full_sha.startswith(lab_sha) or len(lab_sha) < 7:
        return False, f"the lab server runs {lab_sha or 'nothing'}, not {full_sha[:10]} - release it there first"
    hours = (now - switched_at) / 3600
    if hours < GATE_HOURS:
        return False, f"the lab server has run {full_sha[:10]} for {hours:.0f} h; it needs {GATE_HOURS} h"
    return True, f"ran {hours / 24:.1f} days on the lab server"


def ci_verdict(check_runs: list[dict]) -> tuple[bool | None, str]:
    """Pure: green = at least one finished check, every one successful (or skipped). None = unknown."""
    if not check_runs:
        return None, "no CI result for this commit"
    pending = [r["name"] for r in check_runs if r.get("status") != "completed"]
    if pending:
        return False, f"CI still running ({', '.join(pending[:3])})"
    failed = [r["name"] for r in check_runs if r.get("conclusion") not in ("success", "skipped", "neutral")]
    if failed:
        return False, f"CI failed ({', '.join(failed[:3])})"
    return True, f"CI green ({len(check_runs)} checks)"


def ci_green(full_sha: str) -> tuple[bool | None, str]:
    url = git("remote", "get-url", "origin")
    slug = url.removesuffix(".git").split("github.com")[-1].lstrip(":/")
    env = dict(os.environ)
    if "GH_TOKEN" not in env and shutil.which("gh"):
        token = subprocess.run(["gh", "auth", "token", "--user", slug.split("/")[0]], capture_output=True, text=True)
        if token.returncode == 0:
            env["GH_TOKEN"] = token.stdout.strip()
    if not shutil.which("gh"):
        return None, "the gh command isn't installed: CI can't be checked"
    out = subprocess.run(["gh", "api", f"repos/{slug}/commits/{full_sha}/check-runs", "--jq", ".check_runs"],
                         capture_output=True, text=True, env=env)
    if out.returncode != 0:
        return None, f"CI can't be read ({out.stderr.strip()[:120]})"
    return ci_verdict(json.loads(out.stdout or "[]"))


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
    print(f"connected: {probe.stdout.decode().strip()}\nsaved in {CONFIG.relative_to(ROOT)}"
          " - next: deploy.py replace-ssh-key, then passwords and setup")


def cmd_replace_ssh_key(args) -> None:
    """A login key made on this laptop replaces the one AWS made (which passed through another laptop)."""
    cfg = load_config()
    new = Path(args.key_file).expanduser() if args.key_file else Path.home() / ".ssh" / "apnasor-real-ed25519"
    if not new.exists():
        new.parent.mkdir(parents=True, exist_ok=True)
        made = subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C",
                               f"apnasor-real {datetime.now(UTC):%Y-%m-%d}", "-f", str(new)])
        if made.returncode != 0:
            fail("ssh-keygen could not make the key")
    public = Path(str(new) + ".pub").read_text(encoding="utf-8").strip()
    line = shlex.quote(public)
    remote(cfg, f"grep -qxF {line} ~/.ssh/authorized_keys || echo {line} >> ~/.ssh/authorized_keys")
    trial = {**cfg, "key": str(new.resolve())}
    if subprocess.run(ssh_cmd(trial) + ["true"], capture_output=True).returncode != 0:
        fail("the new key doesn't open the server - the old one still works; nothing was removed")
    remote(trial, f"umask 077 && echo {line} > ~/.ssh/authorized_keys.new && mv ~/.ssh/authorized_keys.new "
                  "~/.ssh/authorized_keys")
    if subprocess.run(ssh_cmd(trial) + ["true"], capture_output=True).returncode != 0:
        fail("after removing the old key the new one stopped working - use EC2 Instance Connect to repair")
    CONFIG.write_text(json.dumps(trial, indent=2), encoding="utf-8")
    print(f"done: only {new.name} (made on this laptop) opens the server now; the AWS-made key no longer works.\n"
          "Delete every copy of the old .pem (ask whoever else had it), and keep the new key on this laptop only.")


def cmd_passwords(args) -> None:
    from dotenv import dotenv_values

    from backend.gateway.middleware.auth import USERS
    from backend.gateway.middleware.passwords import hash_password, password_problem

    current = dotenv_values(LOGINS) if LOGINS.exists() else {}
    print("Real login passwords for the REAL-MONEY server only (different from the laptop's and the lab's).")
    print("Enter keeps a password set before.\n")
    for user in sorted(USERS):
        key = f"AUTH_PASSWORD_HASH_{user.upper()}"
        password = getpass.getpass(f"New password for {user}: ")
        if not password:
            if not current.get(key):
                fail(f"{user} needs a password: the real-money server has no demo login")
            continue
        if problem := password_problem(user, password):
            fail(f"{user}: {problem}")
        if getpass.getpass("Same password again: ") != password:
            fail("the two passwords differ - nothing was saved")
        current[key] = hash_password(password)
    LOGINS.parent.mkdir(parents=True, exist_ok=True)
    LOGINS.write_text("".join(f"{k}={v}\n" for k, v in current.items() if v), encoding="utf-8", newline="\n")
    print(f"\nsaved (hashes only) in {LOGINS.relative_to(ROOT)} - the server gets them with: deploy.py env (or setup)")


def sandbox_key_lines(exchanges: list[str]) -> list[str]:
    from dotenv import dotenv_values

    laptop = dotenv_values(ROOT / ".env") if (ROOT / ".env").exists() else {}
    lines = []
    for exchange in exchanges:
        names = [f"{exchange.upper()}_TESTNET_{part}" for part in EXCHANGE_KEYS[exchange]]
        missing = [n for n in names if not (laptop.get(n) or "").strip()]
        if missing:
            fail(f"{exchange}: {', '.join(missing)} not set in this laptop's .env")
        lines += [f"{n}={laptop[n].strip()}" for n in names]
    return lines


def cmd_setup(args) -> None:
    cfg = load_config()
    name, tarball, _ = make_release(args.ref)
    target = push_kit(cfg, tarball)
    print("\n== preparing the machine (the same preparation as the lab server)")
    remote(cfg, f"sudo bash {KIT}/deploy/lab-server/setup.sh </dev/null")
    if server_stage(cfg) == "live":
        fail("this server is already live - setup is for a new server; use release / env")
    print("\n== settings (rehearsal) and the sandbox keys of this laptop")
    install_env(cfg, "rehearsal", args.exchanges)
    send_keys(cfg, sandbox_key_lines(args.exchanges))
    print("\n== market history")
    seed_history(cfg)
    print("\n== first release")
    remote(cfg, f"sudo bash {KIT}/deploy/real-server/install_release.sh {target} {name} "
                f"{'--test' if args.test else ''} </dev/null")
    print("\nThe real-money server runs in REHEARSAL (sandbox keys). Next: the rehearsal checklist (PHASE_D_PLAN D2).")


def cmd_env(args) -> None:
    cfg = load_config()
    stage = server_stage(cfg)
    install_env(cfg, stage, args.exchanges)
    if not args.no_restart:
        remote(cfg, f"sudo bash {BASE}/current/deploy/real-server/check_settings.sh && sudo systemctl restart apnasor-real")
        print("  gateway restarted with the new settings")


def cmd_keys(args) -> None:
    """Type the REAL keys (hidden); they go straight to the server's root-only keys.env."""
    cfg = load_config()
    lines = []
    print("REAL exchange keys for the real-money server. Typed here, they go over SSH straight into the server's\n"
          "root-only keys.env - nothing is saved on this laptop. Make each key on the exchange as\n"
          "project-info/PHASE_D_PLAN.md section 7 says: trade-only, withdrawals OFF, locked to the server's IP.\n")
    for exchange in args.exchanges:
        for part in EXCHANGE_KEYS[exchange]:
            value = getpass.getpass(f"{exchange} {part.lower().replace('api_', '')}: ").strip()
            if not value:
                fail(f"{exchange} {part}: empty - nothing was sent")
            lines.append(f"{exchange.upper()}_MAINNET_{part}={value}")
    remote(cfg, f"rm -rf {KIT} && mkdir -p {KIT}/deploy && cp -r {BASE}/current/deploy/real-server {KIT}/deploy/")
    send_keys(cfg, lines)
    print("\nNext (read-only, nothing is traded):  deploy.py check-keys")


def find_telegram_chat(token: str) -> tuple[str, str]:
    """The chat that last wrote to the bot: (chat id, who). The owner sends the bot one message first."""
    import httpx

    try:
        reply = httpx.post(f"https://api.telegram.org/bot{token}/getUpdates", json={"timeout": 0}, timeout=20).json()
    except httpx.HTTPError as exc:  # never print the URL: the token is in it
        fail(f"could not reach Telegram ({type(exc).__name__})")
    if not reply.get("ok"):
        fail("Telegram refused the token - copy it again from @BotFather")
    chats = [u["message"]["chat"] for u in reply.get("result", []) if u.get("message", {}).get("chat")]
    if not chats:
        fail("the bot has no message yet - open your bot in Telegram, send it any message, then run this again")
    chat = chats[-1]
    who = chat.get("username") or chat.get("first_name") or chat.get("title") or "?"
    return str(chat["id"]), who


def cmd_telegram(args) -> None:
    """Phone alerts: the bot token (hidden) and your chat go to the server's root-only keys.env."""
    import httpx

    cfg = load_config()
    print("Phone alerts (Telegram). Make a bot with @BotFather (/newbot), send your bot one message, then paste\n"
          "the bot's token here (hidden; it goes to the server only).\n")
    token = getpass.getpass("Bot token: ").strip()
    if not token:
        fail("empty - nothing was sent")
    chat, who = find_telegram_chat(token)
    if input(f"Alerts will go to the Telegram chat of {who} (id {chat}). Right? [y/N] ").strip().lower() != "y":
        fail("not confirmed - nothing was sent")
    remote(cfg, f"rm -rf {KIT} && mkdir -p {KIT}/deploy && cp -r {BASE}/current/deploy/real-server {KIT}/deploy/")
    send_keys(cfg, [f"TELEGRAM_BOT_TOKEN={token}", f"TELEGRAM_CHAT_ID={chat}"])
    remote(cfg, "sudo systemctl restart apnasor-real")
    try:
        httpx.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20,
                   json={"chat_id": chat, "text": "ApnaSOR: phone alerts connected. /status for a summary, "
                                                  "/stop to engage the kill switch."})
    except httpx.HTTPError as exc:
        print(f"note: the test message didn't go out ({type(exc).__name__}); the gateway will try at its start")
    print("done: the gateway restarted with alerts on - your phone gets 'Gateway started' in a minute.")


def cmd_ai_key(args) -> None:
    """Decision A5: the copilot's Claude key (hidden) -> the server's root-only keys.env; checked, or removed again."""
    cfg = load_config()
    remote(cfg, f"rm -rf {KIT} && mkdir -p {KIT}/deploy && cp -r {BASE}/current/deploy/real-server {KIT}/deploy/")
    writer = f"sudo python3 {KIT}/deploy/real-server/write_keys.py {BASE}/shared/keys.env"
    if args.remove:
        remote(cfg, f"{writer} --remove {AI_KEY} </dev/null && sudo systemctl restart apnasor-real")
        print("done: the key is removed and the gateway restarted - the copilot's AI is off "
              "(its quick analysis still works).")
        return
    print("The copilot's AI on the real-money server is Claude (decision A5). Make a key at console.anthropic.com\n"
          "(API keys) and type it here - hidden; it goes straight to the server's root-only keys.env.\n"
          "With it on, what you ask the copilot - and the real balances and orders it looks up to answer - go to\n"
          "Anthropic's API (not used for training). Without it the copilot's quick analysis still works.\n")
    key = getpass.getpass("Claude API key: ").strip()
    if not key:
        fail("empty - nothing was sent")
    if not key.startswith("sk-ant-"):
        fail("that doesn't look like a Claude API key (they start with sk-ant-) - nothing was sent")
    send_keys(cfg, [f"{AI_KEY}={key}"])
    verdict = remote(cfg, f"cd {BASE}/current && sudo bash -c 'source deploy/lab-server/versions.env; "
                          f"source deploy/real-server/lib.sh; as_app_with_keys .venv/bin/python "
                          f"deploy/real-server/check_ai_key.py' </dev/null", capture=True, check=False).strip()
    if not verdict.startswith(("OK", "UNREACHABLE")):
        remote(cfg, f"{writer} --remove {AI_KEY} </dev/null")
        fail(f"Anthropic didn't accept the key ({verdict or 'no answer from the check'}) - it was removed again; "
             "nothing changed")
    remote(cfg, "sudo systemctl restart apnasor-real")
    print(f"{verdict}\ndone: the gateway restarted with the copilot's AI on (Claude)."
          + (" Anthropic couldn't be reached just now; the copilot tries again on its own."
             if verdict.startswith("UNREACHABLE") else ""))


def cmd_check_keys(args) -> None:
    """What the exchanges say about the real keys - from the server's own address (read-only)."""
    remote(load_config(), f"cd {BASE}/current && sudo bash -c 'source deploy/lab-server/versions.env; "
                          f"source deploy/real-server/lib.sh; as_app_with_keys .venv/bin/python "
                          f"scripts/check_exchange_keys.py --mode mainnet' </dev/null")


def cmd_go_live(args) -> None:
    cfg = load_config()
    if server_stage(cfg) == "live":
        fail("this server is already live")
    print("This turns REAL MONEY on for the real-money server:\n"
          f"  - exchanges: {', '.join(args.exchanges)} (each only once its exchange confirms the key is trade-only and\n"
          "    locked to this server's IP)\n"
          "  - the sandbox keys are removed from the server\n"
          "  - the hard caps from .env apply (default $25 per order, $100 per algo, $150 exposure, $10 loss limit)")
    if input("\nType REAL MONEY to continue: ").strip() != "REAL MONEY":
        fail("not confirmed - nothing changed")
    names = remote(cfg, f"sudo awk -F= '/_MAINNET_API_/{{print $1}}' {BASE}/shared/keys.env 2>/dev/null || true",
                   capture=True).split()
    missing = [e for e in args.exchanges if f"{e.upper()}_MAINNET_API_KEY" not in names]
    if missing:
        fail(f"no real keys on the server for {', '.join(missing)} - run deploy.py keys first")
    sandbox = sandbox_key_lines(args.exchanges)  # read now: to put the rehearsal back if the check refuses
    install_env(cfg, "live", args.exchanges)
    remote(cfg, f"rm -rf {KIT} && mkdir -p {KIT}/deploy && cp -r {BASE}/current/deploy/real-server {KIT}/deploy/")
    send_keys(cfg, [], drop="testnet")
    if not remote_ok(cfg, f"sudo bash {BASE}/current/deploy/real-server/check_settings.sh {BASE}/current live"):
        install_env(cfg, "rehearsal", args.exchanges)  # nothing half-switched is left behind
        send_keys(cfg, sandbox)
        fail("the safety check refused real money (reasons above) - the server is back in rehearsal, unchanged")
    remote(cfg, f"sudo touch {BASE}/shared/live && sudo systemctl restart apnasor-real && "
                f"for i in $(seq 1 80); do curl -fsS -o /dev/null http://127.0.0.1:8000/api/health && break; sleep 3; done && "
                f"printf '%s  GO LIVE: real money on ({','.join(args.exchanges)})\\n' \"$(date -u +%Y-%m-%dT%H:%MZ)\" "
                f"| sudo tee -a {BASE}/shared/releases.log >/dev/null")
    print("\nREAL MONEY is on. Next: deploy.py status (each exchange must show its key confirmed), then the\n"
          "first-order checklist in the real-money screen (PHASE_D_PLAN D4).")


def cmd_release(args) -> None:
    cfg = load_config()
    name, tarball, full = make_release(args.ref)
    stage = server_stage(cfg)
    lab_ok, lab_why = lab_has_run(full)
    ci_ok, ci_why = ci_green(full)
    print(f"promotion gate: lab - {lab_why}; {ci_why}")
    passed = lab_ok and ci_ok is True
    if stage == "live" and not passed and not args.reason:
        fail("not promoted: real money runs only code that ran >= 3 days on the lab server with CI green.\n"
             "       If you must, release with --reason \"why\" (it is written into the server's release log).")
    note = "gate passed" if passed else (f"OVERRIDE: {args.reason}" if args.reason else "gate not enforced (rehearsal)")
    target = push_kit(cfg, tarball)
    remote(cfg, f"sudo bash {KIT}/deploy/real-server/install_release.sh {target} {name} "
                f"{'--test' if args.test else ''} </dev/null")
    remote(cfg, f"printf '%s  release {name}: %s\\n' \"$(date -u +%Y-%m-%dT%H:%MZ)\" {shlex.quote(note)} "
                f"| sudo tee -a {BASE}/shared/releases.log >/dev/null")


def cmd_rollback(args) -> None:
    remote(load_config(), f"sudo bash {BASE}/current/deploy/real-server/rollback.sh </dev/null")


def cmd_status(args) -> None:
    remote(load_config(), f"bash {BASE}/current/deploy/real-server/status.sh </dev/null")


def cmd_logs(args) -> None:
    cfg = load_config()
    if args.follow:
        subprocess.call(ssh_cmd(cfg, tty=True) + ["journalctl", "-u", "apnasor-real", "-f", "-o", "cat"])
    else:
        remote(cfg, f"journalctl -u apnasor-real -n {int(args.lines)} --no-pager -o cat")


def cmd_tunnel(args) -> None:
    cfg = load_config()
    print(f"Tunnel open: http://127.0.0.1:{args.local_port} on this laptop = the REAL-MONEY server's gateway.\n"
          "In another terminal:  npm --prefix frontend run dev:real-server   then open http://localhost:5190\n"
          "(the page shows a red REAL MONEY SERVER badge). Ctrl+C here closes the tunnel; the server keeps running.")
    subprocess.call(ssh_cmd(cfg, "-N", "-o", "ExitOnForwardFailure=yes", "-L", f"{args.local_port}:127.0.0.1:8000"))


def cmd_backup_pull(args) -> None:
    cfg = load_config()
    name = remote(cfg, f"sudo systemctl start apnasor-real-backup.service </dev/null; ls -1 {BASE}/shared/backups | tail -n 1",
                  capture=True).strip().splitlines()[-1]
    dest = LOCAL / "backups"
    dest.mkdir(parents=True, exist_ok=True)
    cmd = ["scp", "-q", "-r", "-i", cfg["key"], "-P", str(cfg["port"]), "-o", "StrictHostKeyChecking=accept-new",
           "-o", "IdentitiesOnly=yes", f"{cfg['user']}@{cfg['host']}:{BASE}/shared/backups/{name}", "."]
    if subprocess.run(cmd, cwd=dest).returncode != 0:
        fail("could not copy the backup")
    for packed in (dest / name).glob("*.gz"):
        with gzip.open(packed, "rb") as fin, open(packed.with_suffix(""), "wb") as fout:
            shutil.copyfileobj(fin, fout)
    print(f"backup {name} is in {(dest / name).relative_to(ROOT)}")


def cmd_ssh(args) -> None:
    subprocess.call(ssh_cmd(load_config(), tty=True))


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up, update and look after the REAL-MONEY server (over SSH).")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("init", help="remember the server and check the login")
    p.add_argument("--host", required=True, help="the server's fixed IP (Elastic IP)")
    p.add_argument("--key", required=True, help="the .pem key file")
    p.add_argument("--user", default="ubuntu")
    p.add_argument("--port", type=int, default=22)
    p = sub.add_parser("replace-ssh-key", help="a login key made on this laptop replaces the AWS-made one")
    p.add_argument("--key-file", help="where to keep the new key (default ~/.ssh/apnasor-real-ed25519)")
    sub.add_parser("passwords", help="the real-money server's own login passwords")
    exchanges = {"type": lambda s: [e.strip().lower() for e in s.split(",") if e.strip()],
                 "default": ["binance", "bybit"], "help": "exchanges (default binance,bybit - decision A2)"}
    p = sub.add_parser("setup", help="once: machine, settings, sandbox keys, first release (rehearsal)")
    p.add_argument("--ref", default="HEAD")
    p.add_argument("--exchanges", **exchanges)
    p.add_argument("--test", action="store_true", help="also run the unit tests on the server")
    p = sub.add_parser("env", help="send fresh settings (caps, symbols, passwords) and restart")
    p.add_argument("--exchanges", **exchanges)
    p.add_argument("--no-restart", action="store_true")
    p = sub.add_parser("keys", help="type the REAL keys (hidden) - straight to the server")
    p.add_argument("--exchanges", **exchanges)
    sub.add_parser("check-keys", help="what the exchanges say about the real keys (read-only)")
    sub.add_parser("telegram", help="phone alerts: the bot token (hidden) and your chat -> the server")
    p = sub.add_parser("ai-key", help="the copilot's Claude key (hidden) -> the server (decision A5)")
    p.add_argument("--remove", action="store_true", help="remove the key: the copilot's AI goes off")
    p = sub.add_parser("go-live", help="turn real money on (removes the sandbox keys)")
    p.add_argument("--exchanges", **exchanges)
    p = sub.add_parser("release", help="put a commit on the server (the promotion gate when live)")
    p.add_argument("--ref", default="HEAD")
    p.add_argument("--reason", help="release although the gate didn't pass (written into the release log)")
    p.add_argument("--test", action="store_true")
    sub.add_parser("rollback", help="go back to the release before")
    sub.add_parser("status", help="what the server is doing")
    p = sub.add_parser("logs", help="the gateway's log")
    p.add_argument("-n", "--lines", default=100)
    p.add_argument("-f", "--follow", action="store_true")
    p = sub.add_parser("tunnel", help="reach the server's gateway from this laptop (the real-money screen)")
    p.add_argument("--local-port", type=int, default=TUNNEL_PORT)
    sub.add_parser("backup-pull", help="make a fresh backup and copy it to this laptop")
    sub.add_parser("ssh", help="a shell on the server")
    args = parser.parse_args()
    {"init": cmd_init, "replace-ssh-key": cmd_replace_ssh_key, "passwords": cmd_passwords, "setup": cmd_setup,
     "env": cmd_env, "keys": cmd_keys, "check-keys": cmd_check_keys, "telegram": cmd_telegram, "ai-key": cmd_ai_key,
     "go-live": cmd_go_live,
     "release": cmd_release, "rollback": cmd_rollback, "status": cmd_status, "logs": cmd_logs,
     "tunnel": cmd_tunnel, "backup-pull": cmd_backup_pull, "ssh": cmd_ssh}[args.command](args)


if __name__ == "__main__":
    main()
