# Contributing — Team Workflow

> Simple workflow for our 4-person team. Read once, follow the loop.
> **New on the team? Start with [`ONBOARDING.md`](ONBOARDING.md)** (clone, `.env`, first run, the relay).
> Before any work: [`CLAUDE.md`](CLAUDE.md) (project guide and rules) → [`PROGRESS.md`](PROGRESS.md) (state + who is working on what) → [`project-info/ROADMAP.md`](project-info/ROADMAP.md) (what "done" means).

---

## The model in one picture (since 2026-10-07)

```
feature/crypto-pivot   ← THE branch. Everyone works here — backend AND frontend — ONE person at a time (a relay)
theme/liquid-glass     ← Mahnoor + Maham's redesign (glass look + copilot drawer), kept as a theme, not the product
feature/backend-v2, feature/frontend-v2   ← retired 2026-10-07 (history only, don't work on them)
```

- **One branch, one person at a time.** Sarmad decides who has the relay. Nobody works in parallel, so there are no merges and no conflicts.
- **Taking the relay:** message the group ("taking it") → `git pull` → read the newest **handoff note** in `PROGRESS.md` → continue from its "next step".
- **Handing it on:** tests + type-check pass → commit → `git push` → CI ✅ on GitHub → add a handoff note (done, half-done with file + function, exact next step) → push → message the group ("free, pushed").
- **Finish small pieces and push them.** Never leave work only on your laptop: the next person starts from what is on GitHub.
- The product is built in phases (ROADMAP *Full product*): all algorithms → research and prediction tools → bots and agents (proven on testnet) → real money. Backend and the pages that use it are built together.
- The repo is **private**; collaborators sign in with their own GitHub account. `main` keeps the June POC until `feature/crypto-pivot` is merged into it later.

In `DEVELOPMENT_JOURNAL.md` everyone **appends** at the end.

---

## Setup (each person, once)

Full step-by-step version with checks and troubleshooting: [`ONBOARDING.md`](ONBOARDING.md).

```powershell
git clone -b feature/crypto-pivot https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git apnasor
# already have a clone?  git fetch origin; git checkout feature/crypto-pivot; git pull

cd apnasor
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend/requirements.txt -r backend/requirements-dev.txt
cd frontend; npm install; cd ..
Copy-Item .env.example .env
```

**Exchange keys — one shared team set on testnet (team decision 2026-10-06).** The team uses one set of testnet / demo keys, one exchange per owner: **Gate — Fizza's account, OKX — Maham's, Binance + Bybit — Sarmad's**. It is the exchanges' fake money, and four people making three extra accounts each costs more than it saves. (Your own keys still work too: [`project-info/API_KEYS_GUIDE.md`](project-info/API_KEYS_GUIDE.md).)

What sharing costs: each laptop's ledger only knows **its own** fills, so a teammate's trades show up on your Wallet as reconciliation **BREAK**s, and your P&L counts only your own trades. That is expected — don't chase it. Trading, routing, risk checks, TWAPs and the automatic loss limit are not affected (the loss limit uses your own fills only). Rules:
1. **Testnet / demo keys only.** Real-money keys (Phase 8) belong to one person on one laptop and are never shared.
2. **Pass a key privately** — a direct message to the one person who needs it, deleted once it is in their `.env`. Never the group chat, a screenshot or git. Every key: Read + Spot Trade only, never Withdraw.
3. **Before the demo or a recording:** from the night before, only the demo laptop trades. On it, Wallet → **Accept exchange balances** once per exchange → MATCHED. Tell the group when the freeze starts and ends.
4. **A BREAK nobody can explain** (no teammate traded, nobody traded on the exchange website) means the key may have leaked: its owner deletes it on the exchange, makes a new one and sends it privately again.
5. **The kill switch on any laptop cancels open orders on the shared accounts.** ApnaSOR's own orders never stay open (IOC), so normally nothing happens — but a resting order placed by hand on the exchange website would be cancelled.

Never commit `.env`.

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
git checkout feature/crypto-pivot
git pull                                    # what the last person pushed
# read the newest handoff note in PROGRESS.md, then work
python -m unittest discover -s backend/tests -t .
npx --prefix frontend tsc --noEmit -p frontend
python -m ruff check backend scripts start_all.py   # backend only (pip install -r backend/requirements-dev.txt)
git add <files>
git commit -m "feat(copilot): parse notional orders"
git push
```

**Before you push:** tests pass, type-check passes, a handoff note at the top of the relay table in `PROGRESS.md`, a journal entry for anything non-trivial.

**After you push — CI:** GitHub re-runs the checks on a clean machine for every push ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)): backend tests on Linux and Windows, ruff lint, frontend type-check, about 2 minutes. The result is the ✅ / ❌ next to your commit on GitHub (details: the repo's **Actions** tab → your commit → the red job → the failing step). A ❌ is yours to fix before you hand the relay on. A newer push cancels the run for the older one.

---

## Working with AI coding assistants (several people, several sessions)

- **One session = one person on their own clone, one session at a time.** You can't type into a teammate's session. What every session *shares* is the repo itself: the project guide (`CLAUDE.md`, loaded automatically at the start of every session in this folder), `PROGRESS.md` (including the backend handoff notes), `project-info/ROADMAP.md` and `DEVELOPMENT_JOURNAL.md`. An assistant's own memory stays on the computer where it ran. **Anything important must be written into those files**, or the next person won't know it.
- **Start and end every session with the prompts in [`ONBOARDING.md`](ONBOARDING.md) §7.** They make the assistant pull first, respect page ownership or the relay handoff, and push with notes at the end.
- **Commits and PRs carry no tool or AI attribution** — no co-author trailers, no "generated with" lines.

---

## Taking over from someone

`git checkout feature/crypto-pivot`, `git pull`, read their handoff note in `PROGRESS.md`, look at what they did (`git log -p -5`), run the tests, then continue from their "next step". If CI is ❌ on their last commit, fixing it comes first.

**Themes:** `theme/liquid-glass` holds Mahnoor and Maham's redesign (2026-10-07). It is kept as a visual theme to borrow from, not merged as the product. Don't delete it.

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
git pull                      # latest of feature/crypto-pivot
git stash / git stash pop     # shelve changes temporarily
```

Ask Sarmad before anything destructive (`git reset --hard`, `git push --force`). Almost every git problem has a safe fix.
