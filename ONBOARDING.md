# Onboarding — get ApnaSOR running on your machine

> For teammates joining the build. About 30–45 minutes, once.
> When you're done, read [`CLAUDE.md`](CLAUDE.md) (project guide and rules), [`PROGRESS.md`](PROGRESS.md) (where things stand) and [`project-info/ROADMAP.md`](project-info/ROADMAP.md) (what "done" means for your workstream).

**The whole idea in four lines:**
1. Clone the repo straight onto **your team's branch** (it's private; sign in with your own GitHub account).
2. Add **your own** `.env` by hand. It is never in git.
3. Always `git pull` before you start and `git push` when you stop.
4. At milestones Sarmad merges the team branches into `feature/crypto-pivot` (the default branch), and everyone syncs.

**Who works where:**

| People | Branch | How you share it |
|---|---|---|
| **Fizza ⇄ Sarmad** (backend) | `feature/backend-v2` | **Relay**: one of you at a time, never together. Pull, work, push, leave a handoff note in `PROGRESS.md` |
| **Mahnoor + Maham** (frontend) | `feature/frontend-v2` | Same branch, **different pages** (see the table in `PROGRESS.md`). Pull before each session, push after |

---

## 1. Install the tools (skip what you have)

| Tool | Version | Check with |
|---|---|---|
| Git | any recent | `git --version` |
| Python | 3.11 or newer (tested on 3.14) | `python --version` |
| Node.js | 18 or newer (tested on 24) | `node --version` |
| Your AI coding assistant's **desktop app** | latest | — |

Use the assistant in **local** mode on your own computer, not in a cloud session. The exchanges have to be reached from your machine's network, and you need to see the dashboard in your browser.

## 2. Clone — the exact command for you

Open PowerShell in the folder where you keep projects (e.g. `cd D:\`) and run **your** line. It downloads the repo into a folder called `apnasor`, already on your branch:

**Mahnoor and Maham (frontend):**
```powershell
git clone -b feature/frontend-v2 https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git apnasor
```

**Fizza (backend):**
```powershell
git clone -b feature/backend-v2 https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git apnasor
```

**Sarmad** already has the repo at `D:\Sor-Fyp`, so there's no clone:
```powershell
cd D:\Sor-Fyp
git fetch origin
git checkout feature/backend-v2
git pull
```

Then (Mahnoor, Maham, Fizza):
```powershell
cd apnasor
git branch --show-current     # must print your branch name
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

Fill it with **your own** exchange keys. Follow [`project-info/API_KEYS_GUIDE.md`](project-info/API_KEYS_GUIDE.md) step by step. The minimum to trade:

| Exchange | What to create | Variables in `.env` |
|---|---|---|
| Binance | Spot **testnet** key (log in to testnet.binance.vision with GitHub) | `BINANCE_TESTNET_API_KEY`, `BINANCE_TESTNET_API_SECRET` |
| Bybit | **Demo Trading** key (not "Testnet") | `BYBIT_TESTNET_API_KEY`, `BYBIT_TESTNET_API_SECRET` |

Optional: Gate (spot testnet) and OKX (Demo Trading, which also needs a **passphrase**). Read the guide's notes first; both have known problems.

Rules for `.env`:
- Keep `EXCHANGE_MODE=testnet`. Testnet and demo accounts use fake funds.
- **Use your own keys, never someone else's.** If two people trade the same test account, each person's records show the other's trades as mismatches.
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
Open http://localhost:5173 and log in as `admin` / `admin`. Within a few seconds the Market Data page shows live prices and the Wallet shows your test balances. Your **opening balances** are captured automatically the first time. Try one tiny order (e.g. BUY 0.0002 BTC) and open it in the order list to see its routing proof and audit trail. Stop everything with Ctrl+C.

Your local audit database (`data/apnasor-testnet.db`) is created automatically and stays on your machine.

## 6. Your branch and your part

Your branch already exists, and you're on it after cloning. Your part is in the *Who is working on what* table in `PROGRESS.md`:

| Who | Branch | Your part (details in ROADMAP) |
|---|---|---|
| Fizza ⇄ Sarmad | `feature/backend-v2` | In order: LLM copilot API (§5b) → multiprocessing risk + RAFT (§6) → arbitrage detector (§5c) → CI + doc rewrite (§7) |
| Mahnoor | `feature/frontend-v2` | How It Works (rewrite for the crypto design), Dashboard (+ running-TWAP widget), Market Data, Login; later the copilot chat panel |
| Maham | `feature/frontend-v2` | Wallet, Algo Orders, Venue Connectivity, Risk Manager, Kill Switch, Audit Log; responsive / narrow-screen fixes |

**Frontend (two people, one branch):** stay inside your own pages. For the shared files (`services/api.ts`, `stores/dataStore.ts`, `App.tsx`, `components/AppShell.tsx`, `components/shared.tsx`, `theme.ts`, `format.ts`): `git pull` right before you edit, commit and push right after, and tell the other person.

**Backend relay (Fizza ⇄ Sarmad):** never work at the same time.
1. Message the other person ("taking backend").
2. `git pull`.
3. Read the newest **handoff note** in `PROGRESS.md`.
4. Work.
5. Tests pass, push **everything**, then add your handoff note: what's done, what's half-done (file + function), the exact next step.
6. Message the other person ("backend free, pushed").

## 7. Working with your AI coding assistant

Open your `apnasor` folder in the desktop app (local mode). The project guide (`CLAUDE.md`) loads automatically and tells it the rules.

**First message of every session.** Frontend (use your name; your pages are in PROGRESS.md):

> Run `git pull`. Read CLAUDE.md, PROGRESS.md and project-info/ROADMAP.md. I'm **Maham** on the frontend, branch `feature/frontend-v2`. Only touch my pages from the "Who is working on what" table; for shared files, pull first and keep changes small. Plan against the ROADMAP, list risks you find in the code, then build. Verify with the type-check and in the browser.

Backend relay:

> Run `git pull`. Read CLAUDE.md, PROGRESS.md (the newest backend handoff note first) and project-info/ROADMAP.md. I'm **Fizza** on the backend relay, branch `feature/backend-v2`. Continue from the handoff note: plan against the ROADMAP "done" criteria, list risks and contradictions you find in the code, then build. Verify with tests and a small live testnet order.

**Last message of every session:**

> Run the tests and the type-check. Update my row in PROGRESS.md (backend: also add a handoff note at the top of the relay table), append a dated entry to DEVELOPMENT_JOURNAL.md, then commit with a conventional message and push my branch.

Rules:
- **One session at a time.** Two sessions in the same folder overwrite each other's files. Finish and push one before starting the next.
- **No AI or tool attribution** in commits, PRs, code or docs. No co-author lines, no "generated with".
- The assistant's memory stays on your computer. Anything the team needs must be written into `PROGRESS.md`, `DEVELOPMENT_JOURNAL.md` or the code.

## 8. The sync loop

**Every time you sit down:**

```powershell
git pull                                    # what your teammate pushed to your branch
git fetch origin
git merge origin/feature/crypto-pivot       # what Sarmad merged (e.g. new backend endpoints for the frontend)
```

**While you work:** commit small, push often (`git push`).

**Before you push:**
```powershell
python -m unittest discover -s backend/tests -t .
npx --prefix frontend tsc --noEmit -p frontend
```

**Milestones:** when a chunk is done (e.g. the copilot API works, or a set of pages is finished), tell Sarmad. He merges `feature/backend-v2` / `feature/frontend-v2` into `feature/crypto-pivot`, and everyone's next `git merge origin/feature/crypto-pivot` picks it up. That's how the frontend gets the copilot API for its chat panel.

## 9. Don'ts

- Don't commit `.env`, `data/`, logs or build output (`frontend/dist`).
- Don't push to `main` or `feature/crypto-pivot`. Only push your own branch.
- Frontend doesn't change `backend/`, and backend doesn't change `frontend/`, without asking. If the frontend needs a new API field, ask the backend relay.
- Don't start a backend session while the other relay person is working.
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
| Pages load but nothing updates live; the gateway log shows `ws_refused` | Your frontend is older than your backend: the live connection now needs a login as its first message. `git merge origin/feature/crypto-pivot` (brings the new `services/websocket.ts`), restart the frontend, log in again |
| Wallet shows a reconciliation **BREAK** | Your test balances changed outside ApnaSOR (manual trade, testnet reset, someone else using your keys). If you're sure, click **Accept exchange balances** |
| Port 8000 / 5173 already in use | Another gateway or dev server is running. Close it (or the other terminal) |
| Browser shows old UI after adding a file | Restart the frontend (`Ctrl+C`, `python start_all.py` again) |
| Tests fail right after cloning | Check `git branch --show-current` shows your branch and that the venv is active |
| `git pull` says "merge conflict" | Two people edited the same lines. Don't panic and don't force-push: ask the other person or Sarmad, and resolve it together |

Stuck for more than 30 minutes? Ask in the group (without keys or screenshots of `.env`).
