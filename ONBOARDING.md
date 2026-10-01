# Onboarding — get ApnaSOR running on your machine

> For teammates joining the build. About 30–45 minutes, once.
> When you're done, read [`CLAUDE.md`](CLAUDE.md) (project guide and rules), [`PROGRESS.md`](PROGRESS.md) (where things stand) and [`project-info/ROADMAP.md`](project-info/ROADMAP.md) (what "done" means for your workstream).

**The whole idea in four lines:**
1. Clone the repo, which is **private** (you're a collaborator, so sign in with your own GitHub account).
2. Add **your own** `.env` by hand. It is never in git.
3. Make **your own branch** for your workstream and push to it.
4. Open a **pull request** when a chunk is done; Sarmad merges and everyone syncs.

---

## 1. Install the tools (skip what you have)

| Tool | Version | Check with |
|---|---|---|
| Git | any recent | `git --version` |
| Python | 3.11 or newer (tested on 3.14) | `python --version` |
| Node.js | 18 or newer (tested on 24) | `node --version` |
| Your AI coding assistant's **desktop app** | latest | — |

Use the assistant in **local** mode on your own computer, not in a cloud session. The exchanges have to be reached from your machine's network, and you need to see the dashboard in your browser.

## 2. Clone the right branch

```powershell
cd D:\               # or wherever you keep projects
git clone -b feature/crypto-pivot https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git apnasor
cd apnasor
```

- The repo is private. If Git asks you to sign in, use the GitHub account Sarmad invited (a browser window opens).
- `-b feature/crypto-pivot` matters: that's where all the current work is. `main` still has the old June simulator.
- **"Repository not found"?** You're signed in with a different GitHub account, or you haven't accepted the collaborator invite (check your email or github.com/notifications).

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
- Workstream A (LLM copilot) also needs `ANTHROPIC_API_KEY`. Get it from Sarmad privately, never in a group chat.

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

## 6. Claim your workstream and make your branch

Agree with the team who takes what, then:

```powershell
git checkout -b feature/<your-workstream>      # e.g. feature/frontend-v2, feature/raft, feature/arb-docs
```

Edit **your row** in the *Who is working on what* table in `PROGRESS.md` (owner, branch, status). Then publish the branch:

```powershell
git add PROGRESS.md
git commit -m "docs: claim workstream B (frontend)"
git push -u origin feature/<your-workstream>
```

| | Workstream | Branch | Spec |
|---|---|---|---|
| A | LLM copilot | `feature/copilot` | ROADMAP §5b |
| B | Frontend: copilot panel, How It Works update, polish | `feature/frontend-v2` | ROADMAP §5b UI, §7 |
| C | Multiprocessing risk + RAFT cluster | `feature/raft` | ROADMAP §6 |
| D | Arbitrage detector + CI + doc rewrite | `feature/arb-docs` | ROADMAP §5c, §7 |

## 7. Working with your AI coding assistant

Open your `apnasor` folder in the desktop app (local mode). The project guide (`CLAUDE.md`) loads automatically and tells it the rules.

**First message of every session** (adapt the workstream):

> Read CLAUDE.md, PROGRESS.md and project-info/ROADMAP.md. I'm on workstream **B (frontend)**, branch `feature/frontend-v2`. Check the "Who is working on what" table, plan the work against the ROADMAP "done" criteria, list risks and contradictions you find in the code, then build. Verify with tests and a small live testnet order.

**Last message of every session:**

> Run the tests and the type-check. Update my row in PROGRESS.md and the "What's next" list, append a dated entry to DEVELOPMENT_JOURNAL.md, then commit with a conventional message and push my branch only.

Rules:
- **One session per folder.** Want two sessions at once? Give the second its own folder with `git worktree add ..\apnasor-2 feature/<branch>` (copy your `.env` into it).
- **No AI or tool attribution** in commits, PRs, code or docs. No co-author lines, no "generated with".
- The assistant's memory stays on your computer. Anything the team needs must be written into `PROGRESS.md`, `DEVELOPMENT_JOURNAL.md` or the code.

## 8. The sync loop

**Every time you sit down:**

```powershell
git checkout feature/<your-workstream>
git pull
git fetch origin
git merge origin/feature/crypto-pivot       # bring in what has been merged
```

**While you work:** commit small, push often (`git push`). Pushing your own branch never affects anyone else.

**Before you push:**
```powershell
python -m unittest discover -s backend/tests -t .
npx --prefix frontend tsc --noEmit -p frontend
```

**When a chunk is done:** GitHub → **Pull requests** → **New pull request** → base: `feature/crypto-pivot` ← compare: your branch. Write a short title and a few bullets, assign Sarmad. He merges, and after that everyone's next `git merge origin/feature/crypto-pivot` picks it up. (Once `feature/crypto-pivot` itself is merged into `main`, Sarmad will announce it and `main` becomes the base.)

## 9. Don'ts

- Don't commit `.env`, `data/`, logs or build output (`frontend/dist`).
- Don't push to `main` or `feature/crypto-pivot` directly. Use your branch and a PR.
- Don't change files in another workstream's folders without asking. Keep edits to the shared files small and additive: `backend/gateway/dependencies.py`, `backend/gateway/main.py`, `frontend/src/services/api.ts`, `frontend/src/stores/dataStore.ts`, `frontend/src/App.tsx`, `frontend/src/components/AppShell.tsx`, `PROGRESS.md`.
- Don't rewrite files with PowerShell `Get-Content | Set-Content`; it corrupts UTF-8 (`—` becomes `â€”`). Use an editor.
- Don't use mainnet (real-money) keys. That's Phase 8, done together.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `git clone`: Repository not found | Sign in with the invited GitHub account / accept the invite |
| `check_exchange_keys`: AUTH FAILED | Wrong key type (Bybit must be **Demo Trading**), or the secret was copied incompletely. See API_KEYS_GUIDE troubleshooting |
| OKX "network error" / feed disconnected | Some ISPs' DNS blocks okx.com. Known; work without OKX |
| Gate shows no balance | Known: Gate's spot testnet wallet isn't funded. Binance + Bybit are enough |
| Wallet shows a reconciliation **BREAK** | Your test balances changed outside ApnaSOR (manual trade, testnet reset, someone else using your keys). If you're sure, click **Accept exchange balances** |
| Port 8000 / 5173 already in use | Another gateway or dev server is running. Close it (or the other terminal) |
| Browser shows old UI after adding a file | Restart the frontend (`Ctrl+C`, `python start_all.py` again) |
| Tests fail right after cloning | Check you're on `feature/crypto-pivot` (or your branch from it) and that the venv is active |

Stuck for more than 30 minutes? Ask in the group (without keys or screenshots of `.env`).
