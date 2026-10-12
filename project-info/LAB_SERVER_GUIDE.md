# Lab server guide — the bots and the practice lab, 24/7 on AWS

> Current as of 2026-10-12 — **the server is live since 2026-10-12** (Zurich, release `12b3e41`, 8 lab bots). The lab server runs **only** the gateway with the bots and the practice lab, on
> exchange **testnet / demo** (fake funds), day and night, so the lab collects days of samples while the team
> keeps building on the laptop. The product demo still runs locally (`python start_all.py`).
> Kit: [`deploy/lab-server/`](../deploy/lab-server/). One command on the laptop does everything:
> `python deploy/lab-server/deploy.py <command>`.

---

## 1. The idea in one picture

```
 Laptop (development, Phase D)                              AWS lab server (Zurich, t4g.small, Ubuntu 24.04)
   your gateway + frontend, your data/                        ONE release = one commit you chose
   deploy.py ── SSH (port 22, your IP only) ───────────────►  gateway on 127.0.0.1:8000: bots + practice lab
   tunnel: laptop 127.0.0.1:18000 ─── SSH ──────────────────►  its own database, testnet keys only
   npm run dev:lab-server → http://localhost:5180               systemd: restart on crash, health check,
   (the page shows a blue LAB SERVER badge)                     daily backup; nothing else open to the internet
```

What keeps the two sides from disturbing each other:

1. **Separate databases.** The server has its own `apnasor-testnet.db` (orders, bots, the fund, the lab's samples).
   Nothing the laptop does changes it, and the server never touches the laptop's `data/`.
2. **The server runs a release, not the branch.** A release is `git archive` of one commit, copied over SSH.
   Uncommitted or half-built work on the laptop never reaches it; it changes only when you run `deploy.py release`.
3. **No GitHub access on the server** — no token, no clone. It cannot pull anything by itself.
4. **Testnet only.** The install refuses a server set to mainnet, and `deploy.py env` never copies a mainnet key.
5. **The lab runs in one place: the server.** The laptop's old lab bots stay stopped (part C). Two machines
   running lab bots on the same testnet accounts would split the samples across two databases.
6. **The same testnet keys are fine** (the team shares one set, decided 2026-10-06). The test accounts hold far more
   than the lab uses ($1,700 of fake USDT). Expected side effect: reconciliation BREAKs on each Wallet page (each
   ledger only knows its own fills), exactly as with teammates today.

---

## 2. Money: what it costs, and how the account stays alive

| Item (Zurich, `eu-central-2`) | Price | Per month |
|---|---|---|
| t4g.small (2 vCPU Graviton, 2 GB) | ≈ $0.021 / hour | ≈ $15.30 |
| Public IPv4 address | $0.005 / hour | ≈ $3.65 |
| 20 GB gp3 disk | ≈ $0.10 / GB-month | ≈ $2.00 |
| **Total** | | **≈ $21 (≈ $0.70 a day)** |

Measured 2026-10-09: the gateway with only the bots and the lab uses about **7 % of one CPU core and 380 MB**,
so 2 GB is ample (1 GB would work but leaves no margin for the model training at start-up).

**Your account (2026-10-09): $58.32 credits left, the Free plan ends 2026-12-05.**

- On the Free plan the account **closes** when the credits run out or after 6 months, whichever comes first. You
  then have 90 days to upgrade before AWS deletes everything. A closed account means the server stops and the bots
  stop.
- **The RDS database (about $1 a day) goes** — Sarmad deletes it (decided 2026-10-09). ApnaSOR doesn't use it: the
  app keeps everything in SQLite files. Kept alongside the server, it would use up the credits by mid-November and
  close the account. Without it, the server costs about $39 until 2026-12-05 and about **$19 is left**.
- **Before 2026-12-05** do one of these:
  - Upgrade to a paid plan. The credits you have left keep paying the bills; they expire 12 months after the account
    was created. Each month after that costs about $21.
  - Or pull the last backup (`deploy.py backup-pull`) and terminate the server.

  Put a reminder in your calendar for 2026-11-28.
- **Budget alert (free, do it now).** Billing and Cost Management → Budgets → Create budget → *Use a template* →
  *Monthly cost budget*. Set $25 and your email. You get an email when the month heads past $25 (the server
  alone is about $21, so an email means something else is running).

---

## 3. Part A — the AWS console (once, about 15 minutes)

**A1. Region: Europe (Zurich), `eu-central-2`.** Zurich is an *opt-in* region, so switch it on first: account menu
(top right) → *Account* → *AWS Regions* → **Europe (Zurich)** → *Enable*. That takes a few minutes and costs
nothing. Then pick **Europe (Zurich)** in the region menu (top right) before anything else — it is at the bottom of the
Europe list, so scroll down inside the menu.

Why Zurich:
- **Tested 2026-10-09.** Binance (testnet and real market) and Bybit (demo and real market) were called from machines
  in 17 countries. All answered except the US (Binance 451, Bybit 403, the same answers GitHub's US machines got)
  and Hong Kong (the checker gave no result there).
- **Outside the EU.** Binance has served no new orders for EU residents since 2026-07-01 (no MiCA licence), so EU
  regions such as Frankfurt, Stockholm, Ireland and Paris could become a problem. Switzerland isn't in the EU.
- **On neither exchange's restricted list**, and away from any conflict zone.
- **Ruled out elsewhere:**
  - UAE and Bahrain: drone strikes in March 2026, still impaired in September; Bybit also restricts Dubai.
  - Japan: Bybit is restricting residents of Japan in 2026.
  - Singapore and Hong Kong: on Bybit's restricted list.
  - US and Canada: refused or restricted.
  - India: Sarmad's choice.

Part B checks in the first minutes that both exchanges accept the server's address, with your own keys. Fallback, if
ever needed: **Sydney** (`ap-southeast-2`), which passed the same test.

**A2. EC2 → Instances → Launch instances**

| Field | Value |
|---|---|
| Name | `apnasor-lab` |
| Application and OS image | **Ubuntu Server 24.04 LTS**, architecture **64-bit (Arm)**. The wizard may offer Ubuntu 26.04 first: choose 24.04 — the kit is tested on it (newer releases replaced `sudo` and the basic file commands) |
| Instance type | **t4g.small** (it says "Free tier eligible") |
| Key pair | *Create new key pair* → name `apnasor-lab`, type **ED25519**, format **.pem** → it downloads once. Move it to `C:\Users\TLS\.ssh\apnasor-lab.pem`. AWS can never give it to you again; never put it in the repo or a chat. |
| Network settings → Edit | Auto-assign public IP: **Enable**. Security group: *Create* `apnasor-lab-ssh`, one rule: **SSH, source "My IP"**. No HTTP or HTTPS rules: nothing else may be open. |
| Configure storage | **20 GiB gp3** |
| Advanced details → Credit specification | **Standard**. T-instances otherwise default to "unlimited", which can bill extra CPU time. We use about 4 % of the machine, so we never need it. |

Click **Launch instance**, open it, and copy its **Public IPv4 address**.

**A3. A second way in (recommended).** Security groups → `apnasor-lab-ssh` → Edit inbound rules → Add rule:
type **SSH**, source = the prefix list **`com.amazonaws.eu-central-2.ec2-instance-connect`**. After that,
*EC2 → Instances → Connect → EC2 Instance Connect* gives you a terminal in the browser. It works even when your home
IP has changed and the "My IP" rule no longer matches you.

**A4. Good to know.**
- AWS restarts the machine by itself after a hardware fault (automatic recovery is on by default for t4g).
- A **stop/start** gives the machine a new public IP; a **reboot** keeps it. After a new IP, run
  `deploy.py init` again with the new address.
- If you want a fixed address, use an Elastic IP: same price while attached, but billed if you leave it unattached.
  Not needed for the lab.

---

## 4. Part B — from the laptop (once, about 10 minutes)

In `D:\Sor-Fyp`, PowerShell:

```powershell
python deploy/lab-server/deploy.py init --host <the IPv4 address> --key C:\Users\TLS\.ssh\apnasor-lab.pem
python deploy/lab-server/deploy.py passwords        # recommended: the server's own login passwords
python deploy/lab-server/deploy.py setup --test     # machine, settings, market history, first release
python deploy/lab-server/deploy.py check-exchanges  # keys work from the server's address?
python deploy/lab-server/deploy.py status
```

- **init** logs in once and remembers the server in `data/lab-server/server.json`. If Windows says the key file is
  *unprotected*, it prints the one `icacls` command that fixes it.
- **passwords** asks for a login password per user, for the lab server only (the laptop's logins don't change). It
  keeps only scrypt hashes, in `data/lab-server/logins.env`. If you skip it, the server keeps demo logins
  (password = user name), reachable only through your SSH tunnel.
- **setup** does all of this:
  - makes a release of the laptop's `HEAD` commit. Commit and push first; it warns about uncommitted changes and
    commits not yet on GitHub.
  - prepares the machine with `setup.sh`: 2 GB swap, a 500 MB log cap, a service user `apnasor`, uv, and Python
    3.14.3 under `/opt/apnasor`.
  - writes the server's settings from the laptop's `.env`:
    - testnet keys for Binance and Bybit only;
    - `EXCHANGE_MODE=testnet`, `LAB_AUTO_RESUME=true`;
    - arbitrage detector, copilot and RAFT off.

    No Gemini key, no RAFT secret, never a mainnet key.
  - copies the laptop's market history (public candles), so the research models train at the first start.
  - installs the release with `install_release.sh`: its own Python packages from `requirements.lock`, a start check,
    the unit tests (`--test`), systemd units, then waits until the gateway answers.
- **check-exchanges** must show both venues authenticated with test balances. A `451` or `403` means the exchange
  refuses this region: terminate the instance and redo part A in Sydney.
- **status** shows:
  - the release, gateway uptime and restarts;
  - memory, disk and clock;
  - each exchange's live feed;
  - the newest backup;
  - every bot and what the lab has collected, with an **ATTENTION** list.

---

## 5. Part C — start the lab (once)

1. Terminal 1: `python deploy/lab-server/deploy.py tunnel`. Leave it open.
2. Terminal 2: `npm --prefix frontend run dev:lab-server`, then open **http://localhost:5180**. The top bar shows
   **TESTNET** and a blue **LAB SERVER** badge: this page is the server, not your laptop. Your normal page stays on
   http://localhost:5173.
3. Log in as admin, with the password you set or `admin` for the demo login. Go to **Bots & Lab → Practice lab** and
   start the three experiments:

   | Experiment | Testnet USDT |
   |---|---|
   | Profit-cycle threshold test | $1,200 |
   | Advisor: explore | $300 |
   | Advisor: strict | $200 |

   They move $1,700 of fake USDT from the wallet into the fund.
4. Within a minute the first buys appear. Close the page and the tunnel; **the server keeps running**.
5. **On the laptop:** its 9 old lab bots (from 2026-10-08) come back *paused* the next time you start the laptop's
   gateway. Don't resume them. **Stop** each one on the laptop's Bots & Lab page (its cash goes back to the fund),
   then **Sell now** or **Coins to manual** for the lot each one holds. From then on the lab lives on the server only.

---

## 6. Every day, every week

- **Daily, 1 minute:** `deploy.py status`. Read the ATTENTION list. "Nothing needs attention" is the normal answer.
- **Weekly:** `deploy.py backup-pull`. It makes a fresh backup and copies it to `data/lab-server/backups/<date>/`,
  unpacked. Read it on the laptop with `python scripts/lab_status.py --db <that file>`.
- **Lab results:** the Bots & Lab page through the tunnel (scoreboard, costs, comparisons, models). The outcome
  models start at 200 samples each.
- `deploy.py logs` shows the last 100 log lines (`-f` follows them live); `deploy.py ssh` opens a shell on the server.

## 7. Updating the server

Only lab fixes need to go to the server, not everything you build for Phase D. When a commit should go there:

1. Commit it, push it, and wait for CI ✅.
2. Run `python deploy/lab-server/deploy.py release` (this laptop's `HEAD`), or `--ref <commit>`.

`install_release.sh` then does this:
- backs up the databases;
- installs the new release next to the old one;
- switches over and restarts the gateway. The bots pause for 1–2 minutes and resume by themselves.
- waits until the gateway answers. **If the new release isn't healthy within 4 minutes, it goes back to the previous
  one by itself.**

It keeps the newest 3 releases. `deploy.py rollback` goes back one release by hand. Releases never touch the
databases or the keys: those live in `/opt/apnasor/shared/`.

New keys (a new Bybit demo key, say): put them in the laptop's `.env`, then run `deploy.py env`. It rebuilds the
settings, uploads them and restarts the gateway.

## 8. What happens when …

| Event | What happens by itself | What you do |
|---|---|---|
| The gateway crashes | systemd restarts it after 10 s; lab bots resume 60 s after the start (`LAB_AUTO_RESUME`) | nothing; `status` counts restarts |
| The gateway hangs (runs but doesn't answer) | the health check (every 5 min) restarts it after 3 failures in a row | nothing |
| Reboot (AWS maintenance, your reboot) | the gateway starts at boot; lab bots resume | nothing |
| Hardware fault at AWS | AWS automatic recovery restarts the machine; the disk (and the data) stays | nothing |
| Memory runaway | systemd stops it at 1.4 GB and restarts it | look at `logs` |
| An exchange is down / its book stale | bots wait, with the reason on their card | nothing |
| 3 orders in a row refused before any exchange | that bot **pauses** (needs a person) | read the reason (status / page), fix, resume |
| Kill switch engaged | every bot pauses; **never** resumed automatically | release it and resume on the page |
| Binance spot testnet's monthly reset | balances go back to their defaults and the order history is wiped; the bots usually carry on | if a bot pauses, read its reason |
| Bybit demo key expires (**2026-12-29**: keys without an IP list last 90 days) | Bybit orders refused | new key → laptop `.env` → `deploy.py env` |
| Your home IP changes | SSH from the laptop is refused; **the bots keep running** | security group → edit the SSH rule → "My IP", or use EC2 Instance Connect |
| Disk | the databases grow a few MB a day; 14 days of backups; the log is capped at 500 MB | nothing (20 GB lasts years) |
| Credits / Free plan end | the account closes | section 2: RDS decision now, upgrade or wind down before 2026-12-05 |

What "resume automatically" never does:
- touch a bot that a person, the kill switch or the three-refusals rule paused;
- resume anything while the kill switch is engaged;
- run on real money (`LAB_AUTO_RESUME` only acts on lab bots, which are testnet-only).

On the laptop it is off: there, every bot still comes back paused for you to review.

## 9. Security

- Only SSH is open, with your key and from your IP (plus the optional Instance Connect range). The gateway listens on
  `127.0.0.1` only: nobody reaches it except through your SSH tunnel. Even a wrongly opened firewall rule exposes
  nothing.
- The server's settings (`/opt/apnasor/shared/lab.env`, readable by the service user only) hold:
  - testnet keys;
  - login hashes;
  - nothing else.

  The Bybit demo key can trade but not withdraw.
- The `.pem` key exists only on your laptop. If you lose it the bots keep running, and EC2 Instance Connect still
  lets you in (A3).
- No GitHub credentials, no LLM key and no RAFT secret on the server. The service runs as `apnasor` with a read-only
  system and code; it writes only to `/opt/apnasor/shared`.
- Ubuntu installs security updates daily by itself (unattended-upgrades; it never reboots on its own).

## 10. Ending the lab

1. Run `deploy.py backup-pull` and keep the folder.
2. EC2 → Instances → `apnasor-lab` → Instance state → **Terminate**. That deletes the disk too.
3. Delete the key pair and the security group.
4. Release the Elastic IP, if you made one: an unattached one is billed.

## 11. What is where

| On the laptop | |
|---|---|
| `deploy/lab-server/deploy.py` | every command above (init, setup, release, env, passwords, status, logs, check-exchanges, tunnel, backup-pull, rollback, ssh) |
| `data/lab-server/` (gitignored) | `server.json` (address, key path), `lab.env` (the last settings sent), `logins.env` (server password hashes), `releases/`, `backups/` |
| `frontend/.env.lab-server` | the `dev:lab-server` page: gateway at 127.0.0.1:18000 (the tunnel), LAB SERVER badge |

| On the server (`/opt/apnasor`) | |
|---|---|
| `releases/<date>-<commit>/` | the code of one release + its own `.venv`; `current` and `previous` point at two of them |
| `shared/data/` | `apnasor-testnet.db`, `market-history.db`, the login-signing key — kept across releases |
| `shared/lab.env` | the settings and testnet keys (owner-only) |
| `shared/backups/<date_time>/` | gzipped database copies: daily at 22:30 UTC and before every release, 14 days |
| `python/` | Python 3.14.3 (uv-managed) |
| systemd | `apnasor-lab.service` (the gateway), `apnasor-health.timer` (every 5 min), `apnasor-backup.timer` (daily) |

| Kit file | Job |
|---|---|
| `setup.sh` | prepares the machine (once; safe to run again) |
| `install_release.sh` | installs a release, switches, proves it healthy or goes back |
| `rollback.sh`, `lib.sh` | going back one release; shared functions |
| `healthcheck.sh` | the 5-minute check |
| `status.sh` | the one-screen report (`deploy.py status`) |
| `requirements.lock` | every Python package with its exact version (ready-built Linux wheels, arm64 and x86_64) |
| `versions.env` | uv and Python versions, the base folder |
| `systemd/` | the three services and two timers |

## 12. Questions that came up (2026-10-12)

- **Do the bots keep running when the laptop is off?** Yes. They live on the AWS server. Closing the tunnel, stopping the page
  or shutting the laptop down changes nothing for them.
- **Is the laptop in sync with the results when I come back?** It depends on the page:
  - The **server's page** (tunnel + `npm --prefix frontend run dev:lab-server`, port 5180, LAB SERVER badge) shows
    everything live, including every trade made while the laptop was off. The data is stored on the server.
  - The **laptop's own app** (`python start_all.py`, port 5173) shows only the laptop's data, never the server's bots.
    The two are separate on purpose. Because both use the same testnet accounts, the laptop's Wallet shows the server's
    trades as reconciliation BREAKs (expected).
- **Do the bots ask before trading?** On the server, no:
  - **Profit-cycle bots** are automatic by design: buy a small lot, check every 5 seconds, sell only above cost + both
    fees + the threshold, repeat.
  - **The advisor** has two modes. *Ask me* proposes a trade with its reasons and waits (a proposal expires after 10
    minutes). *Auto* approves its own trades within its limits. The two lab advisors run in Auto.
  - **On real money** the advisor's Auto mode is refused by the code, so it always asks. A profit-cycle bot stays
    automatic but can only spend its pocket of the automation fund, and the kill switch stops every bot.
- **Who checks the server every day?** A scheduled check in the Claude desktop app on Sarmad's laptop ("Lab server
  daily check", about 10:07 local time) runs `deploy.py status` read-only and reports. It only runs while that app is
  open. Anyone with the key can run `deploy.py status` by hand.

## 13. Panel questions

- **"Is it deployed?"** The bots and the practice lab run 24/7 on an AWS EC2 server in Zurich, against the
  exchanges' testnet / demo. Real-money trading and the full dashboard run locally (Phase D/E decides the hosting
  for real money).
- **"Why not Docker or Kubernetes?"** It is one Python process with SQLite files. systemd already gives automatic
  restart, a memory limit, a health check, timers and a least-privilege sandbox, with nothing extra to run or pay
  for.
- **"What if it crashes at night?"** It restarts in 10 s and the lab bots continue a minute later. A hang is caught
  within 15 minutes. Every pause and resume is in the audit log with its reason.
- **"Can someone reach it?"** Only SSH with our key from our IP is open; the API itself listens on the machine only.
