# Phase 6 — explained, verified, defended

> What Phase 6 is in plain words, how to check it works on your own machine (≈ 20 min), and how to answer the panel.
> Design details and every live measurement: [`../DEVELOPMENT_JOURNAL.md`](../DEVELOPMENT_JOURNAL.md) → *2026-10-02 — Phase 6* (+ addendum). Status / handoff: [`../PROGRESS.md`](../PROGRESS.md). API contract: [`ROADMAP.md`](ROADMAP.md) §6.
> Built by Fizza on `feature/backend-v2`, 2026-10-02.

---

## In four sentences

The panel said *"Python asyncio isn't real parallelism."* Phase 6 answers it twice. **(1)** Heavy risk calculations now run on several CPU cores at once, in separate processes — and we measured exactly when that helps and when it doesn't. **(2)** The safety settings (kill switch, risk limits, venue blacklist) are kept by **3 separate programs that vote** (RAFT), so if one crashes nothing is lost and trading carries on.

---

## Part A — risk checks on several CPU cores (the risk batch)

**asyncio vs processes, as a kitchen.** asyncio is *one cook juggling many pots*: start the rice, take the next order while it boils — perfect for *waiting* (exchanges answering), which is most of what a router does. *Calculating* is chopping: one cook chops one thing at a time (Python's GIL means threads don't help). More chopping speed needs **more cooks in separate kitchens = separate processes**, each on its own core.

**1. Pre-flight — check a whole plan before sending anything.** Position limit 0.0025 BTC; a TWAP buys 0.004 BTC in 4 slices of 0.001.
- *Before:* each slice was checked only when sent → slices 1-2 bought (0.002 held), slice 3 rejected (0.003 > limit), slice 4 too → TWAP stalled half-done with BTC nobody wanted. (A real bug found in the code.)
- *Now:* all 4 slices are checked before the first is sent → *"Pre-flight: slice 3 of 4 would be rejected by pre-trade risk (position_limit)"* — nothing is bought. Every TWAP shows *"ⓘ Pre-flight: all N slices pass pre-trade risk at today's limits"* on the Algo Orders page.
- `POST /api/risk/preflight` does the same for any basket of orders.

**2. Stress test — "how much could this position lose in the next hour?"** The computer imagines 5,000 possible next hours (price moving minute by minute, as much as the coin has *really* been moving lately — measured from our own live mid prices) and looks at the worst 1%. Live example: holding 0.0017 BTC (~$146) → *worst-1% loss within the hour ≈ $2.15*. ~150 ms of pure calculation per order — the chopping that extra kitchens share.

**3. The honest measurement** (`scripts/benchmark_risk_batch.py`, chart [`benchmarks/risk-batch.svg`](benchmarks/risk-batch.svg); laptop with 4 cores / 8 threads):

| Job | One process | Pool of processes | Verdict |
|---|---|---|---|
| The 7 quick checks, 10,000 orders | 31 ms | 257 ms (4 proc.) | pool **8× slower** → stays in one process |
| Stress test, 4 orders | 648 ms | 283 ms (8 proc.) | **2.3× faster** |
| Stress test, 32 orders | 4,990 ms | 1,855 ms (8 proc.) | **2.7× faster** |

Not 4× on 4 cores: one busy core runs faster than four (Turbo Boost), and handing work to another process costs time. Live inside the gateway: 12-order stress pre-flight 2.0 s in one process → 0.86 s on the pool of 4.
**Panel line:** *"asyncio for waiting, processes for calculating — and we measured where each one wins."*

---

## Part B — the RAFT cluster (3 programs that vote)

**Why.** The kill switch lived in ONE program. Real trading systems don't trust a safety switch to a single process; they keep it in several copies that agree.

**How RAFT works — three secretaries with notebooks.**
- Each **node** keeps a notebook (**log**) of decisions; each decision has a page number (**log index**).
- One is the head secretary — the **leader**. Every decision goes to her; she writes it and tells the other two. It becomes **official (committed)** once **2 of 3** have written it.
- Every 0.1 s the head says "I'm still here" (**heartbeat**). If she faints, the others notice the silence, wait a *random* 0.5-1 s (so they don't both shout at once), and one asks for votes; 2 votes → new head. Each head's period has a number — the **term**.
- **Why nothing is lost:** a secretary only votes for someone whose notebook is at least as complete as hers. Every official decision is in at least 2 notebooks, so the new head always has all of them.

**Live test** (`scripts/raft_demo.py --gateway --twap 0.0006`, testnet): TWAP running → leader killed after 2 slices → **new leader in ~1.5-2 s**, nothing lost → kill switch engaged and replicated to the 2 survivors → released (2 of 3 = majority) → **TWAP completed** → the killed node restarted 15 s later and copied the pages it missed.

**The safety rule.** With 2 of 3 nodes dead (no majority):
- **Engage the kill switch → works at once** (HTTP 200 in ~0.7 s; trading stops in the first millisecond). Stopping is always safe, so a broken cluster must never block it; it is replicated when the nodes return.
- **Release → refused at once** (HTTP 503 in ~0.3 s: *"only 1 of 3 RAFT nodes are reachable … needs a majority (2). Nothing changed."*). Restarting trading must be agreed.
- Same for the rest: blacklisting (tightening) at once; un-blacklisting and changing limits (loosening) need a majority.

**Signed messages.** Every message between the nodes and the gateway carries an **HMAC-SHA256 signature** made with `RAFT_SHARED_SECRET` (from `.env`, never sent anywhere), a time stamp, and on requests a one-time number (nonce) the reply must echo. A program without the secret can neither change nor read anything; a recorded genuine message can't be replayed (older than 30 s or nonce already used → refused). `GET /api/consensus/status` shows `"signed": true` and each node's `rejected_messages`.

**Why the gateway keeps its own copy.** Every order checks the kill switch; asking the cluster each time would slow every order. The gateway keeps the agreed settings locally, re-reads them every 0.5 s and after every change — **trading never waits on the cluster** (that's why the TWAP kept going while the leader was dead).

---

## Where things live

| What | File |
|---|---|
| The 7 checks (one pure function: order button + pre-flight) | `backend/services/risk_engine/checks.py` |
| Pre-flight, stress test, process pool | `backend/services/risk_engine/batch.py`, `preflight.py`, `volatility.py` |
| Gateway ↔ cluster (the safety rule; the ONE way to change kill switch / limits / blacklist) | `backend/services/risk_engine/controls.py` (`svc.risk_controls`) |
| RAFT algorithm (no network, no clock — fully testable) | `backend/services/consensus/core.py` |
| One node process / storage / state / client / signing | `consensus/node.py`, `storage.py`, `state_machine.py`, `client.py`, `wire.py` |
| Demo and benchmark | `scripts/raft_demo.py`, `scripts/benchmark_risk_batch.py` |
| Tests (226 total) | `backend/tests/test_consensus/`, `backend/tests/test_risk_batch/` |

---

## Verify it yourself (≈ 20 min)

Run from the project root with the venv's Python (a plain `python` may lack the packages). If something fails, the *Troubleshooting* list below covers the usual causes.

| # | Do | ✅ Working if |
|---|---|---|
| 1 | `./.venv/Scripts/python.exe -m unittest discover -s backend/tests -t .` | `Ran 262 tests` … `OK` (226 when Phase 6 landed; the count only grows) |
| 2 | `./.venv/Scripts/python.exe scripts/raft_demo.py` (standalone, ~10 s) | `Leader elected` · `An INTRUDER … REFUSED by every node` · `New leader … nothing lost` · `All 3 nodes hold the same state: YES` · `write refused, as it must be` |
| 3 | Make your own secret: `./.venv/Scripts/python.exe -c "import secrets; print(secrets.token_hex(32))"` → in `.env`: `RAFT_ENABLED=true` and `RAFT_SHARED_SECRET=<that code>` (never share it) | — |
| 4 | `./.venv/Scripts/python.exe start_all.py` (keep it open) | first `RAFT node 1/2/3 127.0.0.1:900X (pid …)`, then gateway + frontend |
| 5 | http://localhost:5173 → `admin`/`admin` → Kill Switch page: engage, release → Audit Log | both work; audit shows `Agreed by the RAFT cluster: kill switch ENGAGED … (log index N, term T)` |
| 6 | http://127.0.0.1:8000/docs → `POST /api/auth/login` (`{"username":"admin","password":"admin"}`) → **Authorize** with the token → `GET /api/consensus/status` | `enabled: true`, `signed: true`, a `leader`, `majority_reachable: true`, 3 nodes reachable, `rejected_messages: 0` |
| 7 | (≥ 2 min after start) `POST /api/risk/preflight` with `{"orders":[{"symbol":"BTC/USDT","side":"BUY","quantity":0.001},{"symbol":"ETH/USDT","side":"BUY","quantity":0.02},{"symbol":"SOL/USDT","side":"BUY","quantity":0.3},{"symbol":"XRP/USDT","side":"BUY","quantity":50}],"stress":true}`, then again with `"mode":"serial"` | `executor: "pool (4 processes)"`, all `APPROVED`, each with `var_99_usdt`; the serial run's `elapsed_ms` ≈ 2× larger |
| 8 | Second terminal: `./.venv/Scripts/python.exe scripts/raft_demo.py --gateway --twap 0.0006` (~$51 of testnet funds; watch the Algo Orders page) | `Pre-flight: all 6 slices pass` · `KILLING the RAFT leader` · `New leader … after ~1500 ms` · `TWAP COMPLETED: 0.0006 of 0.0006 BTC`; first terminal: `RAFT node N exited … restarting in 15 s` → `restarted` |
| 9 | When all 3 nodes are back: `./.venv/Scripts/python.exe scripts/raft_demo.py --gateway --no-majority` | `ENGAGE … HTTP 200 … ~700 ms` · `RELEASE … HTTP 503 in ~300 ms: Refused: only 1 of 3 …` · after ~15 s `Agreed by the RAFT cluster: kill_switch (queued while …)` · `RELEASE with the majority back: HTTP 200` |
| 10 | Ctrl+C in the first terminal. Back to normal: `RAFT_ENABLED=false` (keep the secret line) | — |

Optional: `./.venv/Scripts/python.exe scripts/benchmark_risk_batch.py --quick` (~30 s; prints *SLOWER* for the checks, *faster* for the stress test). It overwrites the saved chart — put the official one back with `git restore project-info/benchmarks`.

**Troubleshooting**
- `RAFT_ENABLED=true, but: RAFT needs RAFT_SHARED_SECRET…` → step 3's `.env` line is missing or shorter than 16 characters.
- `No stress test for … yet: only N live price samples` → the gateway needs about a minute of live prices after start-up; try again.
- `Start with all 3 nodes up` (step 9) → wait for `start_all.py` to restart a killed node (15 s).
- A node can't start (port 9001-9003 in use) → an old `start_all.py` or node is still running; close it.
- Two people must not run the same exchange keys (reconciliation BREAKs) — unchanged from before; RAFT data (`data/raft-testnet/`) is per machine.

---

## Questions the panel (or a teammate) may ask

| Question | Answer |
|---|---|
| Why not just threads? | In Python only one thread calculates at a time (the GIL). Separate processes each get their own core. |
| Is the pool always faster? | No — 8× *slower* for the quick checks (they stay in one process), 2.7× faster for the stress test. We only claim what the chart shows. |
| What if the leader dies during trading? | New leader in ~1-2 s, nothing lost, trading never stops — shown live in the middle of a TWAP. |
| What if 2 of 3 nodes die? | The kill switch still stops trading at once; releasing it is refused until a majority is back. |
| Can another program fake a message? | No. Every message is signed (HMAC-SHA256) with a secret only our machine has, time-stamped and one-time, so even a recorded genuine message can't be replayed. The demo shows an intruder refused. (Production: a certificate per node — mutual TLS; same idea.) |
| How do you know your RAFT is correct? | Unit tests for every rule, plus a randomized test: 40 runs with random crashes, restarts and network cuts (~27 elections each), checking after every step that there is never more than one leader per term and no two nodes ever disagree on a decision. |
| Is the stress test real data? | The paths are simulated (Monte Carlo); how big the moves are comes from our live market prices, measured continuously. |
| Are 3 processes on one laptop "distributed"? | They are separate OS processes that only talk over TCP; pointing `RAFT_CLUSTER_NODES` at other machines' addresses is the same code (not demoed). |

**Honest limits** (normal for a textbook RAFT core; say them calmly): no log compaction (the log only holds operator actions — tiny), fixed 3-node membership, no pre-vote (a node cut off alone keeps raising its term; harmless), one shared secret instead of per-node certificates, and the gateway's copy can be up to 0.5 s behind a change made by *another* client.
