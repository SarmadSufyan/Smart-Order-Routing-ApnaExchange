# Security hardening (ROADMAP §7) — explained, decided, verified, defended

> What was unsafe, every option we considered, what we chose and why, how it works, how to check it on your own machine, and how to answer the panel.
> Design notes and live results: [`../DEVELOPMENT_JOURNAL.md`](../DEVELOPMENT_JOURNAL.md) → *2026-10-06 — Security hardening*. Status / handoff: [`../PROGRESS.md`](../PROGRESS.md). How the **exchange** API keys are protected is a separate document: [`EXCHANGE_API_KEY_SECURITY.md`](EXCHANGE_API_KEY_SECURITY.md).
> Built by Fizza on `feature/backend-v2`, 2026-10-06. Earlier in §7: the `/ws` login (Sarmad, 2026-10-05), CI (Fizza, 2026-10-06).

---

## In four sentences

Until 2026-10-06 anyone who had read our GitHub repo could **forge an admin login** (the secret that signs logins was written in the code), **any website** open in the same browser could log in as `admin` / `admin` and trade, and the three logins were **password = user name, written in the code**. Now each machine signs logins with **its own random key**, only **our own frontend** may use the API from a browser (REST and the live `/ws` connection), and users get **real passwords stored only as scrypt hashes** in `.env` — the demo login `admin` / `admin` stays only as a testnet convenience on developers' machines and is impossible on mainnet. Every login and failed login is now in the audit log. `python scripts/security_check.py` tries all five attacks against a running gateway (read-only) and says which still work.

### What each teammate has to do

| Who | What |
|---|---|
| Everyone (developer machines) | **Nothing.** Pull, restart. You are logged out once ("Your session expired") — log in again with `admin` / `admin` as before. Nothing to add to `.env`. |
| Whoever prepares the **demo laptop** | Give all three users a real password (*Demo laptop checklist* below), then run `python scripts/security_check.py` — every line must say *refused*. |
| Someone who opens the frontend from another device (a phone on the Wi-Fi) | Add that address to `CORS_ORIGINS` in `.env` (*Part B*). |

---

## Words used below

- **Login ticket (JWT token)** — what the gateway gives the browser after a successful login; every later request shows it. It is *signed*: a stamp only the gateway can make.
- **Signing key (`JWT_SECRET`)** — the secret the stamp is made with. Whoever knows it can make a valid ticket for any user, **without any password**.
- **Origin** — the website a page comes from (`http://localhost:5173` for our frontend). Browsers tell our API which origin is calling.
- **CORS** — the browser's rule for which origins may read our API's answers. The gateway tells the browser which origins are allowed.
- **Hash** — a one-way fingerprint of a password: easy to check a typed password against, impossible to turn back into the password. **Salt** — a random value mixed in, so two people with the same password get different hashes. **scrypt** — a hash that is slow and needs memory *on purpose*, so guessing is expensive.

---

## Part A — the login-signing key (`JWT_SECRET`)

**The hole.** `config.py` had `jwt_secret = "apna-exchange-poc-secret-change-in-prod"`, and `.env.example` a placeholder. Nobody's `.env` set its own, so every gateway signed with a value that is on GitHub. Shown live on the old code: a ticket made with it opened the admin-only `GET /api/admin/metrics` (HTTP 200) — no password.

**Options considered**

| | 1. Created automatically per machine ✅ chosen | 2. Required in `.env` (Sarmad's first note) |
|---|---|---|
| What happens | First start creates a random 256-bit key in `data/jwt-secret-<mode>.key` (gitignored, per machine, like the database) and reuses it | The gateway and `start_all.py` refuse to start until `.env` has `JWT_SECRET=…` (same pattern as `RAFT_SHARED_SECRET`) |
| Teammates | nothing to do | everyone adds a line before their next start |
| Security | random per machine, never in git | the same |

**Decision (Fizza, 2026-10-06): option 1** — equally safe and costs nobody anything six days before the deadline. In both options there is no default in the code, and a `JWT_SECRET` set in `.env` still wins.

**How it works** (`gateway/config.py`, `gateway/middleware/auth.py`)
- `load_jwt_secret()` runs first thing in the gateway's start-up (`main.py: lifespan`) and keeps the key in memory. Order: a real `JWT_SECRET` in `.env` → otherwise this machine's key file (`load_or_create_secret`: `secrets.token_hex(32)`, written to a temp file and renamed, so a crash never leaves half a key; owner-only permissions where the OS supports them).
- Values that were ever public in this repo (`PUBLIC_JWT_SECRETS`: the old default, the `.env.example` placeholder, two from the old deployment doc) count as **not set** — the start-up log says it ignored one.
- A `JWT_SECRET` someone set **on purpose** but shorter than 32 characters **stops the start** with an explanation instead of being quietly replaced (`jwt_secret_problem`; `start_all.py` checks it before starting anything).
- Separate keys for testnet and mainnet (`jwt-secret-testnet.key` / `-mainnet.key`): a testnet ticket never works on a mainnet gateway.
- The key is a `SecretStr` (never in a log); the start-up log only says where it came from (`login_signing_key_ready`).
- Refresh tokens are checked by the same module (`decode_refresh_token`) — the refresh endpoint used to read the secret itself.

**Log everyone out at once** (e.g. a ticket leaked): stop the gateway, delete `data/jwt-secret-testnet.key`, start again — every ticket ever issued stops working.

---

## Part B — which websites may use the API (CORS, and `/ws`)

**The hole.** `main.py` had `allow_origins=["*"], allow_credentials=True`. With that combination our web framework (Starlette 1.7) answers *every* website "yes, you may" by echoing its address back. So a page on any website, open in the same browser while ApnaSOR runs, could call `POST /api/auth/login` with `admin` / `admin`, **read the ticket**, then place orders or release the kill switch — the trader never sees it. Shown live on the old code with a pretend site `https://evil.example`: allowed, ticket readable. And CORS doesn't cover WebSockets at all: the same page could open `/ws` (it then still needed a ticket — which it could get as above).

**Options considered**

| Option | Verdict |
|---|---|
| Keep `*` | ❌ the hole above |
| One fixed address (`http://localhost:5173`) | ❌ breaks when Vite moves to 5174 (port busy) or someone uses `127.0.0.1` |
| **This machine's frontend on any port** (`localhost` / `127.0.0.1`), and an exact list in `.env` when needed ✅ chosen | covers every way we run it locally; a list for anything else |
| Also check the website on `/ws` ✅ chosen | CORS doesn't protect WebSockets; the browser does say the origin, so the gateway checks it itself |

**Decision (Fizza, 2026-10-06):** as recommended.

**How it works**
- Default (`CORS_ORIGINS` empty): an address matching `http(s)://localhost` or `http(s)://127.0.0.1`, any port (`config.py: LOCAL_ORIGIN_REGEX`, matched in full — `http://localhost.evil.example` does **not** match).
- `CORS_ORIGINS=http://localhost:5173,http://192.168.1.20:5173` in `.env` = **only** those exact addresses. `*` in it **stops the start** with an explanation (`cors_problem`).
- No credentials (we use a ticket in a header, never cookies); only the methods and headers we use (`GET POST PUT`, `Authorization`, `Content-Type`).
- `/ws` (`websocket/ws_manager.py: serve`): a browser page from an origin that isn't allowed is refused **before the connection opens** (HTTP 403, logged `ws_refused … website not allowed`). Programs (our scripts) send no origin and still need a ticket as their first message (Sarmad's 2026-10-05 rule).
- One rule for both: `Settings.origin_allowed()`.

**Honest limit:** CORS is a *browser* rule. A program running on the laptop itself isn't stopped by it — that's what the passwords (Part C) are for. And "any port on localhost" trusts every web page served from this laptop (another dev server too) — fine for development; the demo laptop runs only ours.

---

## Part C — the logins (users and passwords)

**The hole.** Three users in `middleware/auth.py` (`HARDCODED_USERS`) with **password = user name**, compared as plain text; the Login page pre-filled `admin` / `admin` and showed all three as buttons. Anyone at the keyboard — or a panel member — is admin in two seconds, and the code itself documents the password. Also, the Login page said "All sessions are audited" — **logins were not audited at all.**

**Options considered**

| | **A. Real passwords if set, demo login as a fallback** ✅ chosen | B. Real passwords required | C. Keep and say so honestly |
|---|---|---|---|
| What happens | A user with a hash in `.env` needs that password; a user without one keeps `admin`/`admin`-style demo login — testnet only | The gateway refuses to start until all three users have hashes | No code change; documented as "demo logins, local only" |
| Teammates | nothing changes | everyone makes 3 hashes before their next start; scripts always ask | nothing |
| Demo laptop | real passwords, no demo buttons | real passwords | the panel types `admin` / `admin` and is in |
| Risk left | a developer machine is as open as before (local only + CORS) | none of these | anyone at the keyboard is admin |

**Decision (Fizza, with Sarmad's OK, 2026-10-06): A**, with one extra rule: **on mainnet there are no demo logins** (real money needs real passwords). It is the only option that is both safe in front of the panel and costs the team nothing during the last week.

**How it works**
- **Users** stay defined in code — one per role (`USERS` in `middleware/auth.py`). Their **passwords are not in code any more**: `.env` holds `AUTH_PASSWORD_HASH_ADMIN`, `…_TRADER1`, `…_RISK_MGR` (`SecretStr`, never logged).
- **The hash** (`middleware/passwords.py`): `scrypt:<N>:<r>:<p>:<salt>:<hash>`, Python's standard library (`hashlib.scrypt`, no new package). Settings N=2^14, r=8, p=5 — one of the scrypt settings OWASP's *Password Storage Cheat Sheet* lists; 16 MiB of memory and **0.5–1 s per check on our laptop (measured)**. A random 16-byte salt per password; compared in constant time. The settings are stored in the string, so they can be raised later without breaking old hashes. Out-of-range settings, or a plain password pasted where a hash belongs, **stop the start** (`hash_problem` / `login_setup_problems`).
- **Making a password:** `python scripts/hash_password.py admin` asks twice (nothing shows while you type), refuses < 10 characters or the user name, and prints the one line for `.env`.
- **Checking a login** (`authenticate_user`): real hash → only the real password; no hash → the demo login (password = user name) **only if `EXCHANGE_MODE` isn't mainnet**. An unknown user name costs as much as a wrong password (checked against a throw-away hash).
- **Never blocks trading:** password checks run **one at a time on their own thread** (`check_login`, a one-thread pool). The order-book feeds never wait for a login; a flood of logins can't occupy the thread pool the audit store writes through; guessing is capped at about one try per second.
- **Mainnet:** demo logins off; if *no* user has a real password the gateway refuses to start ("nobody could log in").
- **Audited:** `login` ("admin logged in", "(demo login)" when it was one) and `login_failed` (actor `unknown`; the typed name, cleaned and cut to 40 characters; never the password), both with the client address.
- **The Login page** asks `GET /api/auth/demo-logins` (no login needed) which users still have the demo login and shows buttons — and pre-fills `admin` / `admin` — **only for those**. On the demo laptop the list is empty: no buttons, empty fields.
- **Start-up says which mode you're in:** `start_all.py` prints `Logins  DEMO for admin, trader1, risk_mgr (password = user name)` or `Logins  real passwords only`; the gateway log has `logins` (+ a `demo_logins_active` warning).
- `scripts/arb_watch.py` and `scripts/raft_demo.py` try the demo login and ask for the admin password (hidden) when it's refused.

**Change / remove / forgot a password:** run the script again and replace the line (only one line per user — with the same name twice, the later line wins); delete the line to go back to the demo login; restart the gateway either way. Old tickets of that user stay valid until they expire (60 min) — delete the key file (Part A) to end them at once.

---

## Demo laptop checklist (before the panel)

1. Your **own** exchange keys in `.env` (two gateways on the same accounts show each other's fills as BREAKs). VPN off.
2. `python scripts/hash_password.py admin`, then `trader1`, then `risk_mgr` → paste the three lines into `.env`. Keep the passwords somewhere safe (a password manager or on paper with the presenter).
3. `python start_all.py` → it must print `Logins    real passwords only (no demo logins)`.
4. Open http://localhost:5173 → the Login page shows **no "DEMO ACCOUNTS"** and empty fields; `admin` / `admin` → "Invalid username or password"; the real password works.
5. `python scripts/security_check.py` → **every line "refused"**, ending "Every attack was refused." (exit code 0).
6. Audit Log → the test logins appear (`login`, `login_failed`).

---

## Verify it yourself (≈ 5 min)

1. `python start_all.py` (your normal `.env`), then `python scripts/security_check.py`. Expected on a **developer machine**: lines 1-4 **refused**, line 5 (`admin` / `admin`) **works** — "Only the demo login - fine on a developer's machine". Our frontend: ok.
2. Log in on http://localhost:5173 as before; Audit Log shows `admin logged in (demo login)`.
3. Optional — try "demo laptop mode" without touching your `.env`: in PowerShell, `$env:AUTH_PASSWORD_HASH_ADMIN = "<line from hash_password.py, after the =>"`, then `python start_all.py` from that window: the admin demo button is gone, `admin` / `admin` is refused, your password works, and `security_check.py` line 5 says refused. Close the window to go back.
4. `python -m unittest backend.tests.test_api.test_logins backend.tests.test_api.test_security_settings backend.tests.test_api.test_websocket_auth -v` — the rules above, one test each.

What the check printed on 2026-10-06 (this laptop): **old code** (before §7) — all 4 browser/forgery attacks worked (forged admin ticket HTTP 200; `evil.example` allowed, ticket readable; `/ws` opened). **New code, developer machine** — 1-4 refused (401 / 400 / no allowed origin / 403), 5 works. **New code, real passwords** — all 5 refused, exit code 0.

---

## Questions the panel (or a teammate) may ask

| Question | Answer |
|---|---|
| Where are the passwords stored? | Nowhere. `.env` holds only scrypt hashes — fingerprints that can't be turned back into the password, salted, and deliberately slow (0.5–1 s each), so even a copied `.env` can't be guessed quickly. |
| Why scrypt and not bcrypt / Argon2? | It's in Python's standard library (no extra dependency to audit), it's memory-hard (expensive on GPUs), and it's one of OWASP's recommended choices; Argon2 would need a package. |
| I saw `admin` / `admin` in the code — can I log in with it? | Not on this laptop: every user has a real password, so the demo login is off (try it). It exists only on developers' machines on testnet, and never on mainnet. |
| Can someone fake a login ticket? | Only with the signing key — random, 256 bits, created on this machine, never in git or in the code. A ticket made with the old public value is refused (the check script shows it). |
| Can a malicious website use your API from my browser? | No. Only our own frontend's address is allowed (CORS), and the live connection checks the website itself; a test page from another site is refused (shown: HTTP 400 / 403). |
| What if someone guesses passwords? | Each try costs ~1 s and checks run one at a time, so at most about one guess per second — and every failed attempt is in the audit log with its address. |
| Who logged in when? | The Audit Log: every login and failed login, with the user and the address. |
| Is this production-grade? | It's the right shape (no secrets in code, hashed passwords, least-privilege origins, audit), at POC scale: three fixed users, no account lock-out, no password-change screen, one laptop. A real deployment would add an identity provider (SSO), HTTPS, and per-user accounts — the same checks would sit behind it. |
| Does the frontend show it? | The Login page shows demo buttons only for users who still have the demo login — none on the demo laptop. |

**Honest limits** (say them calmly): three users fixed in code (one per role); no lock-out after failed tries (a lock-out would let anyone lock the admin out — we rate-limit and audit instead); no password-change screen (the script + a restart); a password change doesn't end that user's open sessions until they expire (delete the key file to end them all); the key file has the same protection as `.env` (the user's folder permissions on Windows); HTTP, not HTTPS, on localhost (fine on one machine; a network deployment needs TLS).

---

## Where things live

| File | What |
|---|---|
| `backend/gateway/config.py` | `JWT_SECRET` (`SecretStr`, no default), `PUBLIC_JWT_SECRETS`, `jwt_secret_problem`, `resolved_jwt_secret_path`; `CORS_ORIGINS`, `LOCAL_ORIGIN_REGEX`, `cors_problem`, `origin_allowed`; `AUTH_PASSWORD_HASH_*`, `password_hash_for`, `demo_logins_allowed` |
| `backend/gateway/middleware/passwords.py` | `hash_password`, `verify_password`, `hash_problem`, `password_problem` (pure, no I/O) |
| `backend/gateway/middleware/auth.py` | `load_or_create_secret`, `load_jwt_secret`; `USERS`; `authenticate_user`, `check_login` (password thread), `demo_logins`, `login_setup_problems`, `login_summary`; tokens: `create_*`, `decode_access_token`, `decode_refresh_token` |
| `backend/gateway/routers/auth.py` | `POST /api/auth/login` (audited), `GET /api/auth/demo-logins`, `POST /api/auth/refresh` |
| `backend/gateway/main.py` | start-up checks + log lines (before anything connects); the CORS middleware |
| `backend/gateway/websocket/ws_manager.py` | `/ws` refuses a page from a website that isn't allowed |
| `start_all.py` | refuses bad settings before starting anything; prints the login mode |
| `scripts/hash_password.py` | makes the `.env` line for a real password |
| `scripts/security_check.py` | tries the five attacks (read-only); exit code 0 = all refused |
| `frontend/src/pages/Login.tsx`, `services/api.ts` | demo buttons only for users with the demo login |
| `backend/tests/test_api/test_security_settings.py`, `test_logins.py`, `test_websocket_auth.py` | 41 tests: the rules above (33, 2026-10-06) + the `/ws` login (8, Sarmad 2026-10-05) |

**Still open in §7 (not part of this change):** `npm audit` findings in the frontend's dependencies (Mahnoor / Maham); `start_all.py` starts the gateway 1.5 s after the RAFT nodes — usually before they have a leader, so the audit log says "RAFT cluster unreachable at start-up" at almost every start (it reconnects a moment later; next backend item).
