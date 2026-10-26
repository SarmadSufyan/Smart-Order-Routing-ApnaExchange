# Onboarding — get ApnaSOR running on your machine

> For teammates joining the build. About 30–45 minutes, once.
> When you're done, read [`PROJECT_GUIDE.md`](PROJECT_GUIDE.md) (project guide and rules), [`PROGRESS.md`](PROGRESS.md) (where things stand) and [`project-info/ROADMAP.md`](project-info/ROADMAP.md) (what "done" means for your workstream).

**The whole idea in four lines:**
1. Clone the repo on **`feature/crypto-pivot`** (it's private; sign in with your own GitHub account).
2. Add your `.env` by hand. It is never in git.
3. **One person works at a time** (a relay, for backend and frontend alike). Sarmad decides who has it.
4. Take the relay: `git pull`, read the newest handoff note. Hand it on: push, write a handoff note, tell the group.

**Since 2026-10-07 everyone works on `feature/crypto-pivot`.** `feature/backend-v2` and `feature/frontend-v2` are retired; Mahnoor and Maham's redesign is kept on `theme/liquid-glass` as a theme.

---

## 1. Install the tools (skip what you have)

| Tool | Version | Check with |
|---|---|---|
| Git | any recent | `git --version` |
| Python | 3.11 or newer (tested on 3.14) | `python --version` |
| Node.js | 18 or newer (tested on 24) | `node --version` |

Run everything on your own computer: the exchanges have to be reached from your machine's network, and you need to see the dashboard in your browser.

## 2. Clone — the exact command for you

Open PowerShell in the folder where you keep projects (e.g. `cd D:\`). It downloads the repo into a folder called `apnasor`:

```powershell
git clone -b feature/crypto-pivot https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git apnasor
cd apnasor
git branch --show-current     # must print feature/crypto-pivot
```

**Already have a clone** (e.g. Sarmad at `D:\Sor-Fyp`, or an old clone on `feature/backend-v2` / `feature/frontend-v2`):
```powershell
git fetch origin
git checkout feature/crypto-pivot
git pull
```

- The repo is private. If Git asks you to sign in, use the GitHub account Sarmad invited (a browser window opens).
- **"Repository not found"?** You're signed in with a different GitHub account, or you haven't accepted the collaborator invite (check your email or github.com/notifications).
- `main` still has the old June simulator. Never work there.

## 3. Install the dependencies

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # do this in every new terminal
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd frontend
npm install
cd ..
```

If PowerShell refuses to run `Activate.ps1`, run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, then retry.

## 4. Create your `.env` (by hand — never committed)

```powershell
Copy-Item .env.example .env
notepad .env
```

Fill it with the exchange keys. **The team shares one set of testnet keys** (decided 2026-10-06): ask each owner privately — Gate: Fizza, OKX: Maham, Binance + Bybit: Sarmad (rules: CONTRIBUTING.md *Exchange keys*). To make your own instead, follow [`project-info/API_KEYS_GUIDE.md`](project-info/API_KEYS_GUIDE.md) step by step. The minimum to trade:

| Exchange | What to create | Variables in `.env` |
|---|---|---|
| Binance | Spot **testnet** key (log in to testnet.binance.vision with GitHub) | `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` |
| Bybit | **Demo Trading** key (not "Testnet") | `BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET` |

Optional: Gate (spot testnet) and OKX (Demo Trading, which also needs a **passphrase**). Read the guide's notes first; both have known problems.

Rules for `.env`:
- Keep `EXCHANGE_MODE=testnet`. Testnet and demo accounts use fake funds.
- **Shared keys mean shared accounts:** a teammate's trades show on your Wallet as mismatches (reconciliation BREAKs). That's expected. Before the demo only the demo laptop trades, and it accepts the exchange balances once. Real-money keys (Phase 8) are never shared.
- **Never commit `.env`** (it's in `.gitignore`), and never paste keys into WhatsApp, group chats, screenshots or the repo.
- Each variable once only. If a name appears twice, the later line silently wins.
- For the LLM copilot, add `GEMINI_API_KEY`: create your **own** free key at https://aistudio.google.com/apikey (Google account → *Create API key*; **don't** enable billing). Without it everything else runs; the copilot just reports itself unavailable.

## 5. Check that everything works

```powershell
python scripts/check_exchange_keys.py
```
Expected: `Binance (V1) OK - authenticated on testnet. Balances: ...` and `Bybit (V4) OK - authenticated on demo. ...`. OKX may show a network error; that's known (see the guide).

```powershell
python -m unittest discover -s backend/tests -t .
```
Expected: `Ran 107 tests ... OK` (the count grows as we add tests).

```powershell
python start_all.py
```
Open http://localhost:5173 and log in as `admin` / `admin` (the demo login of a developer machine; the demo laptop uses real passwords — `project-info/SECURITY_GUIDE.md`). Within a few seconds the Market Data page shows live prices and the Wallet shows your test balances. Your **opening balances** are captured automatically the first time. Try one tiny order (e.g. BUY 0.0002 BTC) and open it in the order list to see its routing proof and audit trail. Stop everything with Ctrl+C — it takes ~3–4 s (the gateway stops cleanly first, then the frontend and the RAFT nodes); Ctrl+C again stops everything at once.

Your local audit database (`data/apnasor-testnet.db`) is created automatically and stays on your machine.

## 6. The relay (how the four of us share the work)

Everyone works on `feature/crypto-pivot`, backend and frontend, **one person at a time**. Sarmad decides who has the relay.
1. Message the group ("taking it").
2. `git pull`.
3. Read the newest **handoff note** in `PROGRESS.md` and continue from its "next step".
4. Work. Push small finished pieces as you go.
5. Tests + type-check pass, push **everything**, check CI ✅ on GitHub, then add your handoff note: what's done, what's half-done (file + function), the exact next step.
6. Message the group ("free, pushed").

## 7. Every session

**Start:** `git checkout feature/crypto-pivot` and `git pull`; read `PROJECT_GUIDE.md`, `PROGRESS.md` (the newest handoff note first) and `project-info/ROADMAP.md`; take the relay (message the group). Plan against the ROADMAP's "done" criteria, list the risks and contradictions you find in the code, then build.

**End:** run the tests and the type-check, add a handoff note at the top of the relay table in `PROGRESS.md`, append a dated entry to `DEVELOPMENT_JOURNAL.md`, commit with a conventional message, push `feature/crypto-pivot`, and check that CI is green.

Rules:
- **One session at a time.** Two sessions in the same folder overwrite each other's files. Finish and push one before starting the next.
- **No AI or tool attribution** in commits, PRs, code or docs. No co-author lines, no "generated with".
- Anything the team needs must be written into `PROGRESS.md`, `DEVELOPMENT_JOURNAL.md` or the code; nothing lives only on one computer.

## 8. The sync loop

**Every time you sit down:**

```powershell
git checkout feature/crypto-pivot
git pull                                    # what the last person pushed
```

**While you work:** commit small, push often (`git push`).

**Before you push:**
```powershell
python -m unittest discover -s backend/tests -t .
npx --prefix frontend tsc --noEmit -p frontend
```

## 9. Don'ts

- Don't commit `.env`, `data/`, logs or build output (`frontend/dist`).
- Don't push to `main`. Push to `feature/crypto-pivot` only while you hold the relay.
- Don't start a session while someone else has the relay.
- Don't work on `feature/backend-v2`, `feature/frontend-v2` (retired) or `theme/liquid-glass` (a kept theme).
- Don't rewrite files with PowerShell `Get-Content | Set-Content`; it corrupts UTF-8 (`—` becomes `â€”`). Use an editor.
- Don't use mainnet (real-money) keys. That's Phase 8, done together.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `git clone`: Repository not found | Sign in with the invited GitHub account / accept the invite |
| `check_exchange_keys`: AUTH FAILED | Wrong key type (Bybit must be **Demo Trading**), or the secret was copied incompletely. See API_KEYS_GUIDE troubleshooting |
| OKX "network error" / feed disconnected | Some ISPs' DNS blocks okx.com. Known; work without OKX |
| Gate shows no balance | Known: Gate's spot testnet wallet isn't funded. Binance + Bybit are enough |
| Binance answers **451** "restricted location", Bybit **403** | A VPN is on. Turn it off for live tests and the demo |
| Pages load but nothing updates live; the gateway log shows `ws_refused` | Your frontend is older than your backend: the live connection needs a login as its first message. `git checkout feature/crypto-pivot` + `git pull`, restart the frontend, log in again |
| Wallet shows a reconciliation **BREAK** | Your test balances changed outside ApnaSOR: a teammate traded on the shared keys (expected), a manual trade, or a testnet reset. Click **Accept exchange balances** when you want a clean start. Nobody can explain it? Tell the key's owner (CONTRIBUTING.md *Exchange keys*, rule 4) |
| `admin` / `admin` refused and the Login page shows no demo buttons | This machine has real passwords (`AUTH_PASSWORD_HASH_*` in `.env`): use them, or delete those lines to go back to the demo login (`project-info/SECURITY_GUIDE.md`) |
| `start_all.py` says "Nothing was started" about `JWT_SECRET`, `CORS_ORIGINS` or `AUTH_PASSWORD_HASH_…` | The message says the fix. Usually: delete the line from `.env` (the safe default takes over) |
| Logged out right after pulling the security update ("Your session expired") | Expected once: logins are now signed with your machine's own key. Log in again |
| `start_all.py` says "Port 8000 (gateway) is already in use - by python.exe, pid …" (or 5173 / 9001–9003) | ApnaSOR is already running in another terminal — use that one or press Ctrl+C there — or an earlier start was killed without stopping its children: run the `taskkill /PID … /T /F` line the message prints. Running `npm run dev` yourself? Start with `python start_all.py --no-frontend` |
| Browser shows old UI after adding a file | Restart the frontend (`Ctrl+C`, `python start_all.py` again) |
| `ModuleNotFoundError: No module named 'fastapi'` (or `httpx`, `jose`) when running a script | That terminal runs the system Python, not the project's. Run `.venv\Scripts\Activate.ps1` once in that terminal (prompt starts with `(.venv)`), or call `.venv\Scripts\python.exe` instead of `python` |
| Tests fail right after cloning | Check `git branch --show-current` shows `feature/crypto-pivot` and that the venv is active |
| `git pull` says "merge conflict" | Two people edited the same lines. Don't panic and don't force-push: ask the other person or Sarmad, and resolve it together |

Stuck for more than 30 minutes? Ask in the group (without keys or screenshots of `.env`).
