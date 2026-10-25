"""Report screenshots of every page, 1920x1080, Studio light, from the laptop sandbox (no orders placed).

    python scripts/launch_video/shots.py [out_dir]     default: reports/screenshots-sandbox
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from playwright.async_api import async_playwright

import lib

PAGES = [
    ("/", "01-dashboard", None),
    ("/market-data", "02-market-data", None),
    ("/howitworks", "03-how-it-works", None),
    ("/algos", "04-algo-orders", None),
    ("/research", "05-research", None),
    ("/bots", "06-bots-and-lab", None),
    ("/wallet", "07-wallet", None),
    ("/arbitrage", "08-arbitrage", None),
    ("/risk", "09-risk-manager", None),
    ("/killswitch", "10-kill-switch-raft", None),
    ("/venues", "11-venues", None),
    ("/audit", "12-audit-log", None),
]


async def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    token, user = lib.login_token()
    async with async_playwright() as pw:
        browser, ctx, page = await lib.new_recording_page(pw, "shots", token, user, record=False)
        await page.evaluate("() => { const c = document.getElementById('__cur'); if (c) c.style.display = 'none'; }")
        for path, name, _ in PAGES:
            if (out / f"{name}.png").exists():
                continue
            await page.goto(lib.APP + path, wait_until="networkidle")
            await asyncio.sleep(2.5)
            await page.evaluate("() => window.__cursorVisible && window.__cursorVisible(false)")
            await page.screenshot(path=str(out / f"{name}.png"))
            print(name)
        # the order dialog (form) and the proof of the newest order, without placing one
        await page.goto(lib.APP + "/", wait_until="networkidle")
        await asyncio.sleep(1.5)
        await page.get_by_role("button", name="New order").click()
        await asyncio.sleep(1.0)
        await page.screenshot(path=str(out / "13-new-order-dialog.png"))
        print("13-new-order-dialog")
        await page.mouse.click(30, 900)          # the dialog closes on a click outside it, not on Escape
        await page.get_by_text("New Order", exact=True).wait_for(state="hidden", timeout=5000)
        await asyncio.sleep(0.5)
        await page.locator("tbody tr").first.click()
        await page.get_by_text(re.compile("Audit trail")).first.wait_for(timeout=20000)
        await asyncio.sleep(0.8)
        await page.screenshot(path=str(out / "14-order-proof.png"))
        await page.mouse.move(960, 600)
        await page.mouse.wheel(0, 700)
        await asyncio.sleep(0.6)
        await page.screenshot(path=str(out / "15-order-proof-audit-trail.png"))
        print("14/15 order proof")
        await page.mouse.click(30, 900)
        await page.get_by_text(re.compile("Audit trail")).first.wait_for(state="hidden", timeout=5000)
        await asyncio.sleep(0.5)
        # the copilot drawer (empty, with its suggestions)
        await page.get_by_role("button", name="Copilot", exact=True).click()
        await asyncio.sleep(2.0)
        await page.screenshot(path=str(out / "16-copilot-drawer.png"))
        print("16-copilot-drawer")
        # the login page
        await ctx.clear_cookies()
        await page.goto(lib.APP + "/login", wait_until="networkidle")
        await page.evaluate("() => { sessionStorage.clear() }")
        await page.goto(lib.APP + "/login", wait_until="networkidle")
        await asyncio.sleep(1.5)
        await page.screenshot(path=str(out / "00-login.png"))
        print("00-login")
        await ctx.close()
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "reports" / "screenshots-sandbox"))
