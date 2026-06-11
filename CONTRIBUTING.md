# Contributing — Team Workflow

> How the four of us work on this repo. Read this once when you start; come back to it when a step trips you up. Commands are PowerShell (Windows). Bash equivalents work the same way unless noted.

---

## 0. The mental model in one paragraph

We never edit `main` directly. Every change goes through a short-lived **branch** (one branch per feature/fix), gets reviewed in a **pull request (PR)** on GitHub, and only then gets merged into `main`. `main` is always the latest "good" state — anyone can clone it and the project runs. Branches are throwaway; PRs are the conversation.

---

## 1. First-time setup (each teammate, once)

### 1.1 Install prerequisites

| Tool | Why | Install |
|---|---|---|
| Git | obvious | https://git-scm.com/download/win — accept all defaults; **Git Credential Manager** comes with it |
| GitHub CLI (optional but recommended) | makes auth + PR creation painless | `winget install GitHub.cli` |
| Python 3.11, Node 20 + pnpm, etc. | per your workstream | see [`project-info/DEVELOPMENT_SETUP.md`](project-info/DEVELOPMENT_SETUP.md) |

### 1.2 Configure your Git identity (one time, global)

```powershell
git config --global user.name  "Your Full Name"
git config --global user.email "your_uitu_email@students.uitu.edu.pk"
git config --global init.defaultBranch main
git config --global pull.rebase false   # use merge for `git pull` (simpler for beginners)
```

Use the same email your GitHub account uses, otherwise your commits show as an unknown author.

### 1.3 Authenticate with GitHub

Pick one — easiest first:

**Option A — GitHub CLI (recommended):**
```powershell
gh auth login
# Pick: GitHub.com → HTTPS → Yes (auth git with credentials) → Login with web browser
```

**Option B — Personal Access Token (PAT):**
Generate at https://github.com/settings/tokens → "Tokens (classic)" → Generate new token → scope `repo`. Save the token somewhere. When git asks for a password during `git push`, paste the token instead of your real password. Git Credential Manager caches it after the first time.

### 1.4 Clone the repo

```powershell
cd D:\          # or wherever you keep code
git clone https://github.com/<org-or-username>/<repo-name>.git
cd <repo-name>
```

> Sarmad will share the exact URL once the repo exists.

### 1.5 Set up your workstream

| You are on | Read this | Install |
|---|---|---|
| Backend / SOR | `backend/README.md` | `pip install -r backend/requirements-dev.txt` inside a venv |
| Frontend | `frontend/TEAM_GUIDE.md` | `pnpm install` inside `frontend/` after Fizza's import lands |
| Market simulator | `venues/TEAM_GUIDE.md` | `pip install -r venues/requirements.txt` inside a venv |

Copy `.env.example` to `.env` and fill in any local values:
```powershell
Copy-Item .env.example .env
```
`.env` is gitignored — your local secrets never leave your machine.

---

## 2. Daily workflow (every teammate)

This loop is the whole game. Once it clicks, you'll do it on autopilot.

### 2.1 Sync `main` before you start

```powershell
git checkout main
git pull origin main
```

Always. If `main` moved while you were sleeping, you want your branch to start from the latest version, not yesterday's.

### 2.2 Create a branch for your work

```powershell
git checkout -b feature/dashboard-wiring
```

#### Branch naming

Format: `<type>/<short-kebab-name>`.

| Type | When |
|---|---|
| `feature/` | new functionality |
| `fix/` | bug fix |
| `refactor/` | no behavior change, just cleanup |
| `docs/` | only docs / READMEs / journal |
| `test/` | only tests |
| `chore/` | tooling, config, deps |

Examples: `feature/risk-engine-prechecks`, `fix/v3-degrade-flag-reset`, `docs/api-spec-clarification`, `refactor/order-state-machine-cleanup`.

**One branch = one logical change.** If your branch grows past ~10 changed files or two days of work, consider whether it should be two PRs instead.

### 2.3 Work, commit, repeat

```powershell
# do work...
git add path/to/file1 path/to/file2     # stage specific files (preferred)
# OR
git add -A                               # stage everything (use carefully — could include secrets)

git commit -m "feat(risk): add notional-exposure pre-trade check"
```

#### Commit message format — **conventional commits**

```
<type>(<scope>): <short summary in lowercase, no period>

<optional body — what & why, not how>
```

| Type | When |
|---|---|
| `feat` | new feature |
| `fix` | bug fix |
| `refactor` | restructure without changing behavior |
| `docs` | docs only |
| `test` | tests only |
| `chore` | tooling / deps |

| Scope (pick one) | |
|---|---|
| `backend`, `frontend`, `venues`, `infra`, `docs` | the area the commit touches |

Examples:
- `feat(backend): add kill switch activation endpoint`
- `fix(venues): clamp GBM price floor at 0.01`
- `docs(frontend): expand team guide screen split`
- `refactor(backend): extract NBBO computation into its own module`

#### Commit cadence

Commit often, in small logical chunks. Don't pile a whole day's work into one giant commit — it's painful to review and impossible to revert cleanly. Rule of thumb: if the diff is bigger than 200 lines and isn't a single feature, split it.

### 2.4 Push your branch

```powershell
git push -u origin feature/dashboard-wiring
```

The `-u` (or `--set-upstream`) tells git to remember this branch's remote location. After the first push, subsequent pushes are just `git push`.

### 2.5 Open a Pull Request

Either click the link GitHub prints in the terminal after `git push`, OR use GitHub CLI:

```powershell
gh pr create --title "feat(frontend): wire dashboard to live API + WS" --body @"
## Summary
- Replaced mock venue data with TanStack Query call to GET /api/venues.
- Subscribed to venue_health WS messages via useVenueStore.
- Merged live + REST in the VenueStatusBar component.

## Why
POC use case #5 — Dashboard must show real-time venue health.

## Test plan
- [x] pnpm dev → dashboard renders venue dots
- [x] Degrade V3 via /admin/degrade → dot turns yellow within 5s
- [x] Recover V3 → dot turns green within 5s
"@
```

Or just do it in the browser — same outcome. Required content:

- **Title:** under 70 chars. Conventional-commit style.
- **Summary:** 1–3 bullets. What changed.
- **Why:** 1 sentence. Why this matters / which POC use case it serves.
- **Test plan:** Bulleted checklist of what you tested. Tick what you've actually verified.

### 2.6 Respond to review

A teammate will read your PR. They'll either:
- **Approve** → you can merge.
- **Request changes** → push more commits to the same branch; the PR auto-updates.
- **Comment** → discuss, then either of the above.

Don't take review notes personally. Every PR gets review feedback — that's the point.

### 2.7 After merge, clean up

```powershell
git checkout main
git pull origin main
git branch -d feature/dashboard-wiring          # delete local branch
git push origin --delete feature/dashboard-wiring   # delete remote branch (or click the button on GitHub)
```

---

## 3. Pulling teammates' changes into your local main

You want this **at least once a day** so you don't drift far from `main`.

```powershell
git checkout main
git pull origin main
```

That's it. If you currently have a feature branch checked out with uncommitted changes, either commit first or `git stash` your changes before checking out main.

### Updating your in-progress branch with the latest main

If `main` moved while you were working on a long-lived branch, fold the new commits into your branch so the eventual PR is clean:

```powershell
git checkout feature/your-branch
git merge main
# resolve conflicts if any, then:
git commit              # if there are conflicts, this finalizes the merge
git push                # pushes the merged branch back to GitHub
```

(Advanced users can `git rebase main` instead of `git merge main` — produces a tidier history but requires force-pushing. Stick with `merge` until you're comfortable.)

---

## 4. For Sarmad (repo owner / maintainer)

### 4.1 Initial push (one time, right after `git init`)

See the chat transcript for the exact sequence — short version:

```powershell
cd D:\Sor-Fyp
git init
git branch -M main
git add .
git commit -m "chore: initial repo scaffold with project-info, team guides, and folder structure"
git remote add origin https://github.com/<your-user-or-org>/<repo-name>.git
git push -u origin main
```

### 4.2 Invite teammates

GitHub → repo → **Settings** → **Collaborators and teams** → **Add people** → enter their GitHub usernames or UITU emails → role **Write**. They'll get an email; once they accept they can push branches and open PRs.

### 4.3 Protect `main`

GitHub → repo → **Settings** → **Branches** → **Add branch protection rule**:

- **Branch name pattern:** `main`
- ✅ Require a pull request before merging
- ✅ Require approvals — set to **1** (you don't need 2 for a 4-person FYP team)
- ✅ Require status checks to pass before merging — skip until CI exists
- ✅ Do not allow bypassing the above settings — even for admins, optional but recommended

Save. Now nobody (including you) can push directly to `main` — every change goes through a PR.

### 4.4 Reviewing & merging a PR

When a teammate opens a PR:

1. Open the PR on GitHub. **Files changed** tab — read every diff. Don't just skim.
2. Sanity checks (your responsibility as reviewer):
   - Folder layout matches `project-info/PROJECT_STRUCTURE.md`.
   - No business logic in routers.
   - No `print()` in Python (must be `structlog`).
   - No `any` in TypeScript.
   - No hardcoded venue URLs / secrets.
   - Conventional-commit title.
3. Leave comments on specific lines if something needs fixing. Use **Request changes** if it must change before merge; **Comment** for suggestions; **Approve** if it's good.
4. Once approved + all conversations resolved → click **Squash and merge** (preferred — keeps `main` history clean; the whole PR becomes one commit on `main`).
5. After merge, click **Delete branch** on the PR page.

### 4.5 Handling merge conflicts on a PR

If GitHub says "This branch has conflicts that must be resolved":

- Easy path: tell the PR author to do `git checkout their-branch && git merge main`, fix conflicts locally, push. PR updates automatically.
- If they're stuck: pair on it via screen share. Don't try to resolve conflicts in the GitHub web editor unless they're tiny.

### 4.6 Pulling your own teammates' code to your machine

Same as section 3 — there's nothing special about being the maintainer here. Pull `main` daily.

---

## 5. Useful commands cheat sheet

```powershell
# Where am I, what's changed
git status
git log --oneline -10              # last 10 commits
git diff                           # unstaged changes
git diff --staged                  # staged changes

# Move around
git checkout main                  # switch to main
git checkout -b feature/foo        # create + switch to new branch
git branch                         # list local branches
git branch -a                      # list local + remote branches

# Undo before push
git restore <file>                 # discard unstaged changes to <file>
git restore --staged <file>        # unstage <file> (keeps changes in working dir)
git reset HEAD~1                   # undo last commit, keep changes staged
git reset --soft HEAD~1            # undo last commit, keep changes in working dir

# After push — DON'T undo published commits. Instead:
git revert <commit-sha>            # creates a new commit that undoes the old one

# Sync
git fetch origin                   # see what's on remote without merging
git pull origin main               # fetch + merge main from origin

# Stash (save work without committing)
git stash                          # tuck away current changes
git stash pop                      # bring them back

# See who changed what
git blame <file>                   # line-by-line author info
git log --follow <file>            # full history of one file
```

---

## 6. Common gotchas

- **"fatal: refusing to merge unrelated histories"** — you initialized git locally AND created a non-empty repo on GitHub (e.g., with a README). Fix: `git pull origin main --allow-unrelated-histories`, resolve conflicts, push.
- **Pushed something you didn't mean to (e.g., `.env`)** — for secrets, assume it's compromised, rotate the secret, then `git rm --cached .env && git commit -m "chore: remove leaked .env" && git push`. The file stays in history; don't lie to yourself that it's "deleted."
- **PR is huge and the reviewer is overwhelmed** — close it, split into two PRs. Not the reviewer's fault.
- **Conflicting edits to a shared file** — see section 4.5. The fix is communication first, then git mechanics.
- **You forgot to branch and committed straight to `main` locally** — `git branch feature/x && git reset --hard origin/main` puts your work on a new branch and resets local main to the remote. Then push the new branch.
- **`git push` says rejected because remote has commits you don't have** — `git pull origin <your-branch>` to merge them in, resolve conflicts, push again. Don't force-push to a shared branch.
- **Wrong commit message** — if you haven't pushed yet, `git commit --amend -m "new message"`. After pushing, leave it; rewriting history on a shared branch breaks everyone else's clones.

---

## 7. When things go sideways

Ask Sarmad or the team chat **before** running anything destructive (`git reset --hard`, `git push --force`, `git clean -fd`, deleting branches). Almost every git problem has a non-destructive fix; you just might not know it yet. A 30-second message is cheaper than a lost day's work.

If the AI assistant (Claude / Codex / whoever) is your pair-programmer, paste the exact error message and the last 3 commands you ran. Don't paraphrase.
