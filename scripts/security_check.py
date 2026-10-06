"""Try the attacks ROADMAP §7 closed against a running gateway - read-only, nothing is traded or changed.

    python scripts/security_check.py                          # the gateway on this machine
    python scripts/security_check.py http://127.0.0.1:8001    # another one

Each line says whether the attack works. Run it on the demo laptop before the
panel: every line should say "refused". On a developer's machine line 5 (the
demo login admin / admin) still works on purpose until a real password is set
(scripts/hash_password.py). Exit code 0 = everything refused. Explained in
project-info/SECURITY_GUIDE.md.
"""

import asyncio
import sys
from datetime import UTC, datetime, timedelta

import httpx
from jose import jwt
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "http://127.0.0.1:8000"
OLD_PUBLIC_SECRET = "apna-exchange-poc-secret-change-in-prod"  # was written in config.py, so on GitHub
EVIL = "https://evil.example"  # stands for any website open in the same browser
FRONTEND = "http://localhost:5173"

worked: list[str] = []


def report(question: str, attack_works: bool, detail: str) -> None:
    if attack_works:
        worked.append(question)
    verdict = "WORKS - attack possible" if attack_works else "refused"
    print(f"  {verdict:24s} {question}\n  {'':24s}   {detail}")


def preflight(origin: str) -> dict[str, str]:
    return {"Origin": origin, "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type"}


async def main() -> int:
    print(f"\nApnaSOR security check against {BASE} (read-only)\n")
    async with httpx.AsyncClient(base_url=BASE, timeout=10) as http:
        try:
            await http.get("/api/health")
        except httpx.HTTPError:
            print("  The gateway isn't running there (start it: python start_all.py).")
            return 2

        forged = jwt.encode({"sub": "user_001", "username": "admin", "role": "ADMIN", "name": "Forged",
                             "exp": datetime.now(UTC) + timedelta(minutes=5)}, OLD_PUBLIC_SECRET, algorithm="HS256")
        r = await http.get("/api/admin/metrics", headers={"Authorization": f"Bearer {forged}"})
        report("1. Forge an admin login with the secret that was public on GitHub", r.status_code == 200,
               f"GET /api/admin/metrics (admin only) with the forged ticket -> HTTP {r.status_code}")

        r = await http.options("/api/auth/login", headers=preflight(EVIL))
        allowed = r.headers.get("access-control-allow-origin")
        report(f"2. A page on {EVIL} sends a login through your browser", allowed in (EVIL, "*"),
               f"browser's preflight question -> HTTP {r.status_code}, allowed origin: {allowed}")

        r = await http.post("/api/auth/login", headers={"Origin": EVIL}, json={"username": "admin", "password": "admin"})
        allowed = r.headers.get("access-control-allow-origin")
        report("3. Your browser hands that page an admin login ticket", r.status_code == 200 and allowed in (EVIL, "*"),
               f"login -> HTTP {r.status_code}, allowed origin: {allowed}")

    ws_url = BASE.replace("http", "ws", 1) + "/ws"
    try:
        async with connect(ws_url, origin=EVIL, open_timeout=5) as ws:
            await ws.close()
        report(f"4. A page on {EVIL} opens the live /ws connection", True, "connection opened")
    except InvalidStatus as exc:
        report(f"4. A page on {EVIL} opens the live /ws connection", False,
               f"handshake -> HTTP {exc.response.status_code}")

    async with httpx.AsyncClient(base_url=BASE, timeout=10) as http:
        r = await http.post("/api/auth/login", json={"username": "admin", "password": "admin"})
        listing = await http.get("/api/auth/demo-logins")
        demo = (", ".join(listing.json()["demo_logins"]) or "none") if listing.status_code == 200 else "unknown"
        report("5. Log in as admin / admin (the demo login) from a program on this machine", r.status_code == 200,
               f"login -> HTTP {r.status_code}; users still on the demo login: {demo}")

        r = await http.options("/api/auth/login", headers=preflight(FRONTEND))
        ok = r.headers.get("access-control-allow-origin") in (FRONTEND, "*")
        print(f"\n  {'ok' if ok else 'BROKEN':24s} our own frontend ({FRONTEND}) may use the API")

    print()
    if not worked:
        print("  Every attack was refused.\n")
        return 0 if ok else 1
    print(f"  {len(worked)} of 5 attacks work here.", end=" ")
    if [w[:2] for w in worked] == ["5."]:
        print("Only the demo login - fine on a developer's machine; on the demo laptop give every user a real "
              "password (python scripts/hash_password.py <user>) and run this again.\n")
    else:
        print("This gateway runs code from before ROADMAP §7's security fixes.\n")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
