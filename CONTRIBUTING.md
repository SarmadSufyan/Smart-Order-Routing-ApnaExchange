# Contributing — Team Workflow

> Simple workflow for our 4-person team. Read once, follow the loop.
> Before any work: [`CLAUDE.md`](CLAUDE.md) (project guide and rules) → [`PROGRESS.md`](PROGRESS.md) (state + who is working on what) → [`project-info/ROADMAP.md`](project-info/ROADMAP.md) (what "done" means).

---

## The model in one picture

```
main  ◄── PR ── feature/copilot      (workstream A: LLM copilot)
      ◄── PR ── feature/frontend-v2  (workstream B: frontend)
      ◄── PR ── feature/raft         (workstream C: parallelism + RAFT)
      ◄── PR ── feature/arb-docs     (workstream D: arbitrage detector, CI, docs)
```

- **One branch per workstream.** Work freely on it, push often.
- **One PR when a chunk is done**, into `main`. Sarmad reviews and squash-merges.
- **Base branch:** `main` once the `feature/crypto-pivot` PR is merged. Until then, branch from `feature/crypto-pivot`.
- Before starting, **claim your workstream** in the *Who is working on what* table in `PROGRESS.md`, so two people (or two assistant sessions) never build the same thing.

### Suggested workstreams (owners: fill in PROGRESS.md)

| | Workstream | Branch | Mostly touches | Spec |
|---|---|---|---|---|
| A | LLM copilot (Phase 5b) | `feature/copilot` | `backend/services/agents/`, `backend/gateway/routers/copilot.py` | ROADMAP §5b |
| B | Frontend (copilot panel, How It Works update, polish) | `feature/frontend-v2` | `frontend/` | ROADMAP §5b UI + §7 |
| C | Parallelism + RAFT (Phase 6) | `feature/raft` | `backend/services/consensus/`, `scripts/` | ROADMAP §6 |
| D | Arbitrage detector + CI + doc rewrite | `feature/arb-docs` | `backend/services/algos/`, `.github/`, `project-info/` | ROADMAP §5c, §7 |

**Shared files that cause merge conflicts** — keep your edits there small and additive (append, don't reorder): `backend/gateway/dependencies.py`, `backend/gateway/main.py`, `frontend/src/services/api.ts`, `frontend/src/stores/dataStore.ts`, `frontend/src/App.tsx`, `frontend/src/components/AppShell.tsx`, `PROGRESS.md`. In `DEVELOPMENT_JOURNAL.md` everyone **appends** at the end; on a conflict keep both entries.

---

## Setup (each person, once)

```powershell
git clone https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git
cd Smart-Order-Routing-ApnaExchange
git checkout feature/crypto-pivot          # or main after the merge
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
git checkout feature/<your-workstream>
git pull                                    # what your teammate pushed
git merge origin/main                       # stay close to the base (fewer surprises at PR time)
# ...work...
python -m unittest discover -s backend/tests -t .
npx --prefix frontend tsc --noEmit -p frontend
git add <files>
git commit -m "feat(copilot): parse notional orders"
git push
```

When the chunk is done: GitHub → **Pull requests** → **New** → base `main` ← your branch → short title + bullets → reviewer Sarmad.

**Before you push:** tests pass, type-check passes, `PROGRESS.md` row updated, journal entry appended for anything non-trivial.

---

## Working with AI coding assistants (several people, several sessions)

- **One session = one person on one clone and one branch.** You can't type into a teammate's session. What every session *shares* is the repo itself: the project guide (`CLAUDE.md`, loaded automatically at the start of every session in this folder), `PROGRESS.md`, `project-info/ROADMAP.md` and `DEVELOPMENT_JOURNAL.md`. An assistant's own memory stays on the computer where it ran. **Anything important must be written into those files**, or the next session won't know it.
- **Two sessions on the same computer → two folders.** Never point two sessions at the same folder; they will overwrite each other's files. Use a git worktree per session:

  ```powershell
  git worktree add ..\apnasor-copilot feature/copilot     # creates the branch's own folder
  git worktree add ..\apnasor-raft feature/raft
  git worktree list
  ```

  Each folder needs its own `.env` (copy yours). Only one gateway can run on port 8000 at a time, so run the live tests one after another.
- **Starting a session.** Paste something like this as the first message:

  > Read CLAUDE.md, PROGRESS.md and project-info/ROADMAP.md. I'm on workstream **A (LLM copilot)**, branch `feature/copilot`. Check the "Who is working on what" table, plan the work against the ROADMAP "done" criteria, list risks and contradictions you find in the code, then build. Verify with tests and a small live testnet order.

- **Ending a session.** Paste something like this as the last message:

  > Run the tests and the type-check. Update my row in PROGRESS.md and the "What's next" list, append a dated entry to DEVELOPMENT_JOURNAL.md, then commit with a conventional message and push my branch only.

- **Commits and PRs carry no tool or AI attribution** — no co-author trailers, no "generated with" lines.

---

## For Sarmad (reviewing and merging)

1. Open the PR → **Files changed** → scan the diff.
2. Check: anything outside the workstream's folders? Any `.env`, keys, `data/` or logs committed? Tests and type-check pass?
3. Good → **Squash and merge** → delete the branch. Needs work → comment; they fix and push again.

Branch protection for `main` (set once): Settings → Branches → require a pull request before merging; approvals not required (single reviewer).

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
git merge origin/main         # bring in what's been merged
git stash / git stash pop     # shelve changes temporarily
```

Ask Sarmad before anything destructive (`git reset --hard`, `git push --force`). Almost every git problem has a safe fix.
