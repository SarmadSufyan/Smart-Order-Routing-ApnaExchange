# Starting and stopping ApnaSOR (`start_all.py`) — explained, decided, verified

> What went wrong when starting and stopping the app, what really caused it, every option we considered, what we chose and why, how it works now, how to check it yourself, and how to answer the panel.
> Design notes and live results: [`../DEVELOPMENT_JOURNAL.md`](../DEVELOPMENT_JOURNAL.md) → *2026-10-06 — start_all.py: wait for RAFT, refuse a second start, stop everything in order*. Status / handoff: [`../PROGRESS.md`](../PROGRESS.md).
> Built by Fizza on `feature/backend-v2`, 2026-10-06 (ROADMAP §7).

---

## In four sentences

With RAFT on, almost every start wrote **"RAFT cluster unreachable at start-up"** into the audit log, although the cluster was fine. The real cause was that the gateway was too busy for 2 s to hear the answer. A **second start** on top of a running one wrote into the running gateway's audit database before failing. A **Ctrl+C** hard-killed the gateway before it could pause TWAPs, write "Gateway stopped" and save the arbitrage statistics (only 2 "Gateway stopped" in 29 starts), and a crash could leave the frontend running on its port. Now `start_all.py` waits for a RAFT leader and the gateway keeps asking for up to 5 s, a second start is refused before anything starts, and Ctrl+C stops everything in order: the gateway cleanly first, then the frontend's whole process tree and the RAFT nodes. Ctrl+C a second time stops everything at once.

### What each teammate notices

| Who | What changes |
|---|---|
| Everyone | **Same command** (`python start_all.py`). Ctrl+C now takes ~3–4 s and says what it is doing; press it again to stop at once. Ports are left free after every stop. |
| Someone starting a second copy (or with a leftover from an earlier start) | `start_all.py` refuses and says which program holds which port, and how to stop it. Nothing is started. |
| RAFT on | Start-up waits ~2 s for a leader and prints it (`RAFT  leader: node 2 (term 116), 3 of 3 nodes up - after 1.9 s`). The Audit Log says **"synced"** at start instead of "unreachable". |
| Someone running `npm run dev` themselves | Use `python start_all.py --no-frontend` as before; port 5173 is then not checked. |

---

## Words used below

- **Process** — a running program. ApnaSOR is ~10 of them: 3 RAFT nodes, the gateway (+ 4 risk worker processes), and the frontend. On Windows the frontend is really three: `npm` → `cmd` → `node` (Vite).
- **The venv launcher** — on Windows, `.venv\Scripts\python.exe` is a small program that starts the real Python and waits for it. So every Python child is two processes. Killing the launcher kills its Python; a Ctrl+C or Ctrl+Break is ignored by the launcher and handled by the real Python.
- **Process group** — a set of processes that receive a Ctrl+C together. Normally everything started from a terminal is in one group, so one Ctrl+C reaches all of them at the same instant.
- **Ctrl+Break** — Windows' second "please stop" key. Unlike Ctrl+C, a program can send it to *one* process group. uvicorn (our gateway's web server) treats it exactly like Ctrl+C.
- **Event loop** — the gateway's single worker thread for everything network-related (order books, REST, WebSocket, RAFT). If one job keeps it busy, every other job waits.
- **Port** — the number a server listens on: gateway 8000, frontend 5173, RAFT nodes 9001–9003. Two programs can't listen on the same one.

---

## Part A — "RAFT cluster unreachable at start-up"

### What we saw

From 2026-10-02 to 06, **20 of 21** gateway starts with RAFT on logged `consensus_unreachable`. A few seconds later the gateway caught up (Kill Switch page: 3 of 3), but a panel member reading the Audit Log would see a warning at almost every start.

### What we thought, and what it really was

The handoff note blamed `start_all.py`'s fixed 1.5 s wait ("the nodes haven't elected a leader yet"). **Measured, that was not the reason:** in a live run the nodes had a leader at 14:50:02.9, and the gateway said "unreachable" at 14:50:08.4, more than five seconds later. A watchdog that printed the gateway's stack whenever its event loop stopped moving showed the real cause. Right when the gateway asks the cluster (time limit 0.3 s to connect, 0.5 s to answer), its event loop is **frozen for ~2 s** while ccxt loads the exchanges' market lists: building SSL contexts from the Windows certificate store, and parsing Gate's very long market list. The nodes answer in milliseconds, but the gateway only reads the answers after its time limit has run out, so it counts them as missing.

### Options

| Option | Verdict |
|---|---|
| A1 — Wait longer than 1.5 s in `start_all.py` | **No.** The leader already existed for 5 s; waiting longer changes nothing. |
| A2 — `start_all.py` waits until the nodes report a leader | **Done**, but not enough on its own: it shows the cluster is healthy before the gateway starts and prints it. It can't fix a gateway that can't hear the answer. |
| A3 — The gateway keeps asking at start-up, for up to 5 s, until a leader answers | **Done — the actual fix.** One answer arriving late no longer means "down". Only 5 s with no answer at all is reported as unreachable, and then it's true. It also fixes starts without `start_all.py`. |
| A4 — Ask the cluster before the exchanges start (the event loop is quiet then) | **No.** The cluster's state can engage the kill switch, and that sweeps open orders on the exchanges, so the exchanges must already be running. Re-ordering start-up is riskier than asking again. |
| A5 — Stop ccxt from freezing the event loop (load the markets in a thread) | **Not now.** It is the root of the freeze and also delays the first seconds of market data, but it touches the exchange layer. Logged under *Open questions*. |

**If there is still no leader after 20 s** (`--raft-wait`): start the gateway anyway, with a warning naming the nodes that answer. Decided by Fizza, because the gateway is built for this: engaging the kill switch or blacklisting a venue still works at once, while releasing them or changing limits waits for a majority, and `start_all.py` keeps restarting dead nodes. Refusing to start would let one broken node block the whole app.

**Is it safe to wait up to 5 s?** Nothing can trade during the wait. The gateway hasn't opened port 8000 yet (no requests), TWAPs restored after a restart are paused, and the kill switch saved by this gateway is already in force.

---

## Part B — refuse a second start

### Why it matters more than "port in use"

uvicorn runs the gateway's start-up **before** it takes port 8000. A second gateway therefore opens the same audit database (`data/apnasor-testnet.db`), writes "Gateway started", restores state, and only then fails on the port. Seen live: a second start wrote `gateway_started`, `consensus_unreachable` and `gateway_stopped` into the running gateway's audit log. While starting, a gateway also marks unfinished orders and running TWAPs as stopped ("Gateway stopped — resume manually"), which would happen to the *running* gateway's records. Its frontend moved to port 5174 and kept running after everything else stopped.

### Options

| Option | Verdict |
|---|---|
| B1 — Before starting anything, try to take each needed port for a moment, the same way the real server will | **Chosen.** It answers exactly "would the server's start succeed?", on every system and in every language. |
| B2 — A lock file in `data/` | **No.** After a crash the stale lock needs clean-up logic; the ports are the real shared resource. |
| B3 — Try to connect to each port | **No.** On Windows, connecting to a closed local port takes ~2 s to fail (CLAUDE.md gotcha). |
| B4 — Always check all five ports | **No** (Fizza): only the ports this start will use. 8000 always, 5173 unless `--no-frontend`, 9001–9003 only with RAFT on. |

**How it works:** `port_free()` binds a test socket with the same options asyncio's servers use (no `SO_REUSEADDR` on Windows, where it would let us bind over a live server). The frontend is checked on `127.0.0.1` **and** `::1`, because Vite listens on "localhost", which is IPv6 on Windows. On Windows the message names the program and pid from `netstat -ano`. It recognises the listening line by its empty remote end (`0.0.0.0:0`), because the word "LISTENING" is translated on non-English Windows. The message ends with a ready-made `taskkill /PID … /T /F` line for leftovers.

---

## Part C — Ctrl+C stops everything, in order (and cleanly)

### What happened before

Every child was in the terminal's process group, so one Ctrl+C reached all of them at the same instant: the gateway, its 4 risk workers (each printed a `KeyboardInterrupt` traceback), the nodes and npm. Within the same second `start_all.py` hard-killed them all. The gateway never got to its shutdown code, so there was **no "Gateway stopped" and no arbitrage statistics since the last minute's save**. Running TWAPs weren't paused by the gateway either; the next start found them and paused them. When `start_all.py` stopped things for another reason (the gateway crashed, or a second start failed), it killed only `npm`'s `cmd.exe`, and **Vite kept running** on its port.

### Options

| Option | Verdict |
|---|---|
| C1 — Each child in its own process group; `start_all.py` stops them in order: the gateway with Ctrl+Break (Windows) / SIGINT (Linux, macOS) and waits; the frontend's whole tree (`taskkill /T`, or the process group); the nodes | **Chosen.** Uses what uvicorn already supports (0.49 handles Ctrl+Break), no change in the gateway. |
| C2 — A Windows "Job Object" that kills every child when `start_all.py` dies | **Not now.** It would also cover `start_all.py` being killed from Task Manager, but it is Windows-only ctypes code and gives no clean stop. The port check now catches the leftovers instead. |
| C3 — Start Vite with `node` directly (skip `npm` and `cmd`) | **No.** Fewer processes, but it bypasses `package.json`'s scripts. If the frontend team changes `dev`, `start_all.py` silently diverges. |
| C4 — The gateway watches a pipe from `start_all.py` and stops when it closes | **No.** It would work without a console and when `start_all.py` dies, but needs new code in the gateway for something uvicorn already does. |
| C5 — uvicorn's `--timeout-graceful-shutdown` | **No.** After the time limit it cancels requests still running, which could interrupt an order at an exchange (design rule 6). |

### How it works now

1. **Start:** every child is started in its own process group (`spawn()`). A Ctrl+C in the window therefore reaches only `start_all.py`.
2. **Ctrl+C:** `start_all.py` prints what it does:
   - **Gateway first, cleanly:** it is sent Ctrl+Break (Linux/macOS: SIGINT). uvicorn closes the browser connections and runs the shutdown: TWAPs paused, `gateway_stopped` audited, arbitrage statistics saved, audit database closed. `start_all.py` waits up to **60 s**, because a clean stop lets an order already at an exchange finish (rule 6). Measured: **2.8–2.9 s**.
   - **Frontend:** the whole tree at once (`taskkill /T /F`; on Linux/macOS the process group). Vite holds no state.
   - **RAFT nodes:** Ctrl+Break, up to 5 s each, then killed. A node turns Ctrl+Break into Ctrl+C, which asyncio handles by cancelling its main task. It closes its connections and its SQLite log and logs `raft_node_stopped` (~0.2 s).
3. **Ctrl+C again** while it stops: everything is killed at once. A third press can't interrupt that.
4. **The gateway or the frontend exits by itself** (a crash): the rest is stopped the same way. Before, the frontend survived this.
5. **No console** (e.g. some IDE run buttons): Ctrl+Break can't be sent. `start_all.py` then kills the gateway and says so. This is fail-safe: the next start marks interrupted orders, and reconciliation shows what the exchange did.

**About the node change:** in the first live run, one of the three nodes didn't stop by itself and was killed after 5 s (harmless; RAFT is built for that). Its first version raised the stop signal as an error at whatever line was running. Now Ctrl+Break is turned into Ctrl+C, which is asyncio's own documented path. Since then: **42 of 42** node stops clean in a loop (with and without gateway-like traffic) and in every live run. In the same loop the old version had one slow stop (4.6 s) and one run that hung. That points to the old line, but we couldn't reproduce the hang on demand, so we don't claim the exact cause.

---

## Verify it yourself (≈ 5 min, no orders)

1. `python start_all.py`. With RAFT on, expect `RAFT  leader: node N (term T), 3 of 3 nodes up - after ~2 s` before the gateway line.
2. In a **second** terminal, `python start_all.py` again. Expect `Port 8000 (gateway) is already in use - by python.exe, pid …` (and 5173, 9001–9003), "nothing was started", and exit code 1.
3. Log in at http://localhost:5173, then press **Ctrl+C** in the first terminal. Expect `Gateway stopping cleanly …` → `Gateway stopped (~3 s)` → `Frontend stopped` → `RAFT nodes 1, 2, 3 stopped` → `Done.`
4. Audit Log page after the next start (or `GET /api/audit`): the previous run ends with **Gateway stopped**, and this run starts with **Risk controls synced with the RAFT cluster** (not "unreachable").
5. `netstat -ano | findstr ":8000 :5173 :9001"` prints nothing: no leftovers.

## Questions the panel (or a teammate) may ask

- **"Why does a demo project need a supervisor script?"** — ApnaSOR is about ten processes, and the order matters. The cluster must have a leader before the gateway syncs with it, and the gateway must stop before the cluster, so that it can finish an order and write its final state. One script owns that order, so nobody has to remember it.
- **"What if the gateway is killed hard anyway?"** — Nothing unsafe. The kill switch, limits and blacklist are saved (and replicated with RAFT). The next start marks an order that was executing as interrupted, leaves TWAPs paused, and reconciliation compares our ledger with the exchange. A clean stop just makes the audit log complete.
- **"Why not just wait longer at start-up?"** — Because waiting wasn't the problem: the leader existed, and the gateway was too busy to hear it. We measured that before fixing it.
- **"Can anything trade during the 5 s the gateway waits for the cluster?"** — No. The gateway doesn't accept requests yet, restored TWAPs are paused, and the saved kill switch is already in force.
- **"Why refuse a second start instead of letting it fail?"** — Because it doesn't fail cleanly. It writes into the first gateway's audit database before it notices the port is taken.

## Honest limits

- If `start_all.py` itself is killed (Task Manager, an IDE's stop button), its children keep running. The next start's port check names them and prints the `taskkill` line. (C2 would cover this.)
- ~~**The clean stop could hang after the browser was used**~~ — **fixed 2026-10-06 (night, Sarmad)**: a reset connection could leave Python's Windows event loop counting a connection that was gone, so uvicorn never reached our shutdown and `start_all.py` killed the gateway after 60 s (no "Gateway stopped"). `backend/shared/utils/asyncio_fixes.py` finishes the clean-up asyncio skipped; the gateway and every RAFT node install it at start. Journal 2026-10-06 (night, fix).
- The ~2 s event-loop freeze at gateway start (ccxt loading markets) is still there. It is now harmless for RAFT, but it delays the first seconds of market data (*Open questions*).
- A port taken in the second between the check and the start gives the old failure (the gateway exits, and the rest is stopped).
- The Linux/macOS paths (process groups, SIGINT) pass the unit tests in Linux containers, but `start_all.py` itself was run end-to-end only on Windows, the team's and the demo laptop's system.

## Where things live

| What | Where |
|---|---|
| Port check, leader wait, process groups, ordered stop | `start_all.py` (`port_free`, `busy_ports`, `listeners`, `wait_for_leader`, `spawn`, `ask_to_stop`, `kill_tree`, `stop_all`, `supervise`) |
| Gateway keeps asking the cluster at start-up | `backend/services/risk_engine/controls.py` (`RiskControls._first_contact`, `STARTUP_WAIT_S`) |
| A node stops cleanly on Ctrl+Break | `backend/services/consensus/node.py` (`main`) |
| Tests | `backend/tests/test_start_all.py` (real child and grandchild processes, a real listening socket), `backend/tests/test_consensus/test_risk_controls.py` (`test_startup_*`) |
