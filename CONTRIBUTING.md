# Contributing — Team Workflow

> Simple workflow for our 4-person team. Read once, follow the loop.

---

## Setup (each person, once)

### 1. Install Git

Download from https://git-scm.com/download/win — accept all defaults.

### 2. Set your identity

```powershell
git config --global user.name  "Your Full Name"
git config --global user.email "your_email@students.uitu.edu.pk"
```

Use the same email as your GitHub account.

### 3. Clone the repo

```powershell
git clone https://github.com/SarmadSufyan/Smart-Order-Routing-ApnaExchange.git
cd Smart-Order-Routing-ApnaExchange
Copy-Item .env.example .env
```

### 4. Install dependencies for your workstream

| You work on | Command |
|---|---|
| Frontend | `cd frontend` then `pnpm install` |
| Venues | `python -m venv .venv` then `.venv\Scripts\Activate.ps1` then `pip install -r venues/requirements.txt` |
| Backend | `python -m venv .venv` then `.venv\Scripts\Activate.ps1` then `pip install -r backend/requirements-dev.txt` |

---

## Daily work (everyone)

There are **three branches**, one per workstream:

| Branch | Who | What |
|---|---|---|
| `feature/frontend-poc` | Fizza + teammate | Everything in `frontend/` |
| `feature/venues-poc` | Venue dev | Everything in `venues/` |
| `feature/backend-poc` | Sarmad | Everything in `backend/` |

### Step 1 — Get on your branch

**First time** (one person creates the branch):
```powershell
git checkout -b feature/frontend-poc
git push -u origin feature/frontend-poc
```

**Everyone else** (join the existing branch):
```powershell
git fetch origin
git checkout feature/frontend-poc
```

### Step 2 — Pull before you start working

```powershell
git pull origin feature/frontend-poc
```

Do this every time you sit down to work. It picks up whatever your teammate pushed.

### Step 3 — Work, commit, push

```powershell
# edit your files...
git add <files>
git commit -m "short description of what you did"
git push
```

That's the whole loop. Repeat as many times as you want during the day.

### Step 4 — When the work is done, open a PR

Go to GitHub → **Pull requests** → **New pull request** → base: `main` ← compare: `feature/frontend-poc` → **Create pull request**.

Write a short title and a few bullets about what's included. Assign Sarmad as reviewer.

---

## For Sarmad (reviewing and merging)

When a teammate opens a PR:

1. Open the PR on GitHub → **Files changed** tab → scan the diff.
2. Check: did they touch files outside their folder? Any `.env` or secrets committed?
3. If it looks good → **Squash and merge** → **Delete branch**.
4. If something needs fixing → leave a comment, they fix and push again.

### Branch protection (set once)

GitHub → repo → **Settings** → **Branches** → rule for `main`:
- ✅ Require a pull request before merging
- ❌ Uncheck "Require approvals" (you're the only reviewer)
- Save

This ensures everything goes through a PR but you can merge without needing someone else's approval.

---

## Commit message style

Keep it simple. Format: `type(scope): what you did`

```
feat(frontend): add login page
fix(venues): fix negative price bug
docs(backend): update API examples
```

Types: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`
Scopes: `frontend`, `backend`, `venues`, `infra`, `docs`

---

## Quick reference

```powershell
git status                    # what's changed
git log --oneline -5          # last 5 commits
git pull origin <branch>      # get latest from remote
git stash / git stash pop     # temporarily shelve changes
```

## If something goes wrong

Ask Sarmad before running anything destructive (`git reset --hard`, `git push --force`). Almost every git problem has a safe fix.
