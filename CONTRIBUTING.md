# Contributing — Team Workflow

> Simple workflow for our 4-person team. Read once, follow the loop.
> **New on the team? Start with [`ONBOARDING.md`](ONBOARDING.md)** (clone, `.env`, first run, your branch).
> Before any work: [`CLAUDE.md`](CLAUDE.md) (project guide and rules) → [`PROGRESS.md`](PROGRESS.md) (state + who is working on what) → [`project-info/ROADMAP.md`](project-info/ROADMAP.md) (what "done" means).

---

## The model in one picture

```
feature/crypto-pivot  (default branch — only Sarmad merges into it, at milestones)
   ▲            ▲
   │            └── feature/frontend-v2   Mahnoor + Maham   (same branch, different pages)
   └─────────────── feature/backend-v2    Fizza ⇄ Sarmad    (relay: one at a time)
```

- **Two working branches**, both already on GitHub. Clone straight onto yours (exact commands in [`ONBOARDING.md`](ONBOARDING.md)).
- **Pull before you start, push when you stop.**
- **Backend relay:** Fizza and Sarmad never work at the same time. Whoever takes over pulls, reads the newest **handoff note** in `PROGRESS.md`, continues, then pushes everything and writes a new note.
- **Frontend:** Mahnoor and Maham each own a set of pages (table in `PROGRESS.md`). Shared frontend files: pull right before editing, push right after, tell the other.
- **Milestones:** Sarmad merges `feature/backend-v2` / `feature/frontend-v2` into `feature/crypto-pivot`; everyone then runs `git merge origin/feature/crypto-pivot` to get the other team's work (e.g. new API endpoints for the frontend).
- The repo is **private**; collaborators sign in with their own GitHub account. `main` keeps the June POC until `feature/crypto-pivot` is merged into it later.

| People | Branch | Scope | Spec |
|---|---|---|---|
| Fizza ⇄ Sarmad | `feature/backend-v2` | LLM copilot API → multiprocessing risk + RAFT → arbitrage detector → CI + doc rewrite | ROADMAP §5b, §6, §5c, §7 |
| Mahnoor | `feature/frontend-v2` | How It Works, Dashboard (+ TWAP widget), Market Data, Login; later the copilot chat panel | ROADMAP §5b UI, §7 |
| Maham | `feature/frontend-v2` | Wallet, Algo Orders, Venue Connectivity, Risk Manager, Kill Switch, Audit Log; responsive fixes | ROADMAP §7 |

**Files that cause merge conflicts** — keep edits small and additive (append, don't reorder): frontend `services/api.ts`, `stores/dataStore.ts`, `App.tsx`, `components/AppShell.tsx`, `components/shared.tsx`, `theme.ts`, `format.ts`; backend `gateway/dependencies.py`, `gateway/main.py`; and `PROGRESS.md`. In `DEVELOPMENT_JOURNAL.md` everyone **appends** at the end; on a conflict keep both entries.

---

## Setup (each person, once)

Full step-by-step version with checks and troubleshooting: [`ONBOARDING.md`](ONBOARDING.md).

```powershell
# frontend (Mahnoor, Maham):
git clone -b feature/frontend-v2 https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git apnasor
# backend (Fizza):
git clone -b feature/backend-v2 https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git apnasor

cd apnasor
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd frontend; npm install; cd ..
Copy-Item .env.example .env
```

**Exchange keys — your own, never shared.** Create your **own** testnet/demo accounts and keys by following [`project-info/API_KEYS_GUIDE.md`](project-info/API_KEYS_GUIDE.md), and put them in **your** `.env`. Two people trading the same testnet account break each other's reconciliation (each machine's ledger only knows its own fills). Never commit `.env`, and never paste keys into chat, WhatsApp or screenshots.

Then:

```powershell
python scripts/check_exchange_keys.py      # which venues authenticate
python start_all.py                         # gateway :8000 + frontend :5173 (login: admin / admin)
python -m unittest discover -s backend/tests -t .
```

Your local audit database (`data/apnasor-testnet.db`) is created automatically and stays on your machine.

---

## Daily loop

```powershell
git pull                                    # what your teammate pushed to your branch
git fetch origin
git merge origin/feature/crypto-pivot       # what Sarmad merged at the last milestone
# ...work...
python -m unittest discover -s backend/tests -t .
npx --prefix frontend tsc --noEmit -p frontend
git add <files>
git commit -m "feat(copilot): parse notional orders"
git push
```

**Before you push:** tests pass, type-check passes, your `PROGRESS.md` row updated (backend: handoff note added), journal entry appended for anything non-trivial.

**Milestone done?** Tell Sarmad; he merges your branch into `feature/crypto-pivot`.

---

## Working with AI coding assistants (several people, several sessions)

- **One session = one person on their own clone, one session at a time.** You can't type into a teammate's session. What every session *shares* is the repo itself: the project guide (`CLAUDE.md`, loaded automatically at the start of every session in this folder), `PROGRESS.md` (including the backend handoff notes), `project-info/ROADMAP.md` and `DEVELOPMENT_JOURNAL.md`. An assistant's own memory stays on the computer where it ran. **Anything important must be written into those files**, or the next person won't know it.
- **Start and end every session with the prompts in [`ONBOARDING.md`](ONBOARDING.md) §7.** They make the assistant pull first, respect page ownership or the relay handoff, and push with notes at the end.
- **Commits and PRs carry no tool or AI attribution** — no co-author trailers, no "generated with" lines.

---

## For Sarmad (reviewing and merging)

**Taking over the backend relay from Fizza:** `git checkout feature/backend-v2`, `git pull`, read her handoff note in `PROGRESS.md`, review her commits (`git log -p origin/feature/backend-v2`), run the tests, then continue.

**Milestone merge** (backend or frontend chunk done):
1. Open a PR: base `feature/crypto-pivot` ← `feature/backend-v2` (or `feature/frontend-v2`) → **Files changed** → scan the diff.
2. Check: any `.env`, keys, `data/`, logs or build output committed? Frontend touching `backend/` (or the reverse) without agreement? Tests and type-check pass?
3. Merge with **"Create a merge commit"** — **not** squash, and **don't delete the branch**. These are long-lived shared branches: a squash merge makes the next merge from the same branch replay old work and conflict.
4. Tell the team to run `git fetch origin` + `git merge origin/feature/crypto-pivot`.

Branch protection for `main` and `feature/crypto-pivot` (optional, set once): Settings → Branches → require a pull request before merging; approvals not required (single reviewer).

---

## Commit message style

`type(scope): what you did` — types `feat`, `fix`, `docs`, `refactor`, `test`, `chore`; scopes `backend`, `frontend`, `copilot`, `raft`, `algos`, `docs`, `ci`.

```
feat(copilot): turn "buy $50 of BTC over 2 min" into a TWAP proposal
fix(frontend): keep the wallet table inside its card on narrow screens
test(raft): leader re-election after the leader process dies
```

## Quick reference

```powershell
git status                    # what's changed
git log --oneline -5          # last 5 commits
git pull                      # latest of your branch
git merge origin/feature/crypto-pivot   # bring in what's been merged
git stash / git stash pop     # shelve changes temporarily
```

Ask Sarmad before anything destructive (`git reset --hard`, `git push --force`). Almost every git problem has a safe fix.
