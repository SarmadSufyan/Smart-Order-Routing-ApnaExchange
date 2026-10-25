"""Read-only check of the locators the scenes rely on (opens dialogs, types nothing that is submitted)."""
import asyncio
import re

import lib
from playwright.async_api import async_playwright
from scenes import card_of


async def main() -> None:
    token, user = lib.login_token()
    async with async_playwright() as pw:
        b, ctx, p = await lib.new_recording_page(pw, "chk", token, user, record=False)
        ok = []

        async def check(label, coro):
            try:
                r = await coro
                ok.append(f"OK   {label}: {r}")
            except Exception as e:
                ok.append(f"FAIL {label}: {str(e)[:160]}")

        await p.goto(lib.APP + "/", wait_until="networkidle")
        await check("dashboard Best prices card", card_of(p.get_by_text("Best prices", exact=True)).bounding_box())
        await check("dashboard Live order flow card", card_of(p.get_by_text("Live order flow", exact=True)).bounding_box())
        await check("Routing time card", card_of(p.get_by_text("Routing time", exact=True)).bounding_box())
        await check("first order row", p.locator("tbody tr").first.inner_text())
        await check("theme button", p.locator("button[title='Theme']").count())
        await p.locator("button[title='Theme']").click()
        await check("menu Liquid Glass", p.get_by_role("menuitemradio", name=re.compile("Liquid Glass")).count())
        await check("menu Terminal", p.get_by_role("menuitemradio", name=re.compile("Terminal")).count())
        await p.keyboard.press("Escape")
        await p.get_by_role("button", name="Copilot", exact=True).click()
        aside = p.locator("aside[aria-label='Copilot']")
        await check("copilot aside", aside.count())
        await check("copilot input", aside.locator("input[placeholder^='Ask']").count())
        await check("copilot checkbox", aside.locator("input[type=checkbox]").first.is_checked())
        await p.get_by_role("button", name="Copilot", exact=True).click()
        await p.get_by_role("button", name="Depth sweep").click()
        await asyncio.sleep(1)
        sel = p.locator("select").filter(has=p.locator("option[value='liquidity_sweep']")).first
        await check("sweep select", sel.count())
        await check("qty placeholder", p.locator("input[placeholder^='e.g.']").first.get_attribute("placeholder"))
        await check("qty prefilled", p.locator("input[placeholder^='e.g.']").first.input_value())
        await check("submit button", p.get_by_role("button", name=re.compile("Submit (buy|sell) order")).inner_text())
        await p.keyboard.press("Escape")
        await asyncio.sleep(0.5)
        await check("modal closed", p.get_by_text("New Order", exact=True).count())

        await p.goto(lib.APP + "/algos", wait_until="networkidle")
        over = p.get_by_text("Over", exact=True).locator("xpath=following::input[1]")
        slices = p.get_by_text("Slices", exact=True).locator("xpath=following::input[1]")
        await check("algos qty", p.locator("input[placeholder='e.g. 0.002']").count())
        await check("algos Over value", over.input_value())
        await check("algos Slices value", slices.input_value())
        await check("Auto button", p.get_by_role("button", name=re.compile("Auto")).inner_text())
        await check("Pre-flight button", p.get_by_role("button", name=re.compile("Pre-flight check")).count())
        await check("Start button", p.get_by_role("button", name=re.compile(r"^Start TWAP")).inner_text())
        await check("TWAP card", p.get_by_text(re.compile(r"^TWAP-")).first.inner_text())

        await p.goto(lib.APP + "/howitworks", wait_until="networkidle")
        await check("Very slow", p.get_by_role("button", name="Very slow").count())
        await check("Trace order", p.get_by_role("button", name="Trace order").count())

        await p.goto(lib.APP + "/research", wait_until="networkidle")
        await check("Next hour card", card_of(p.get_by_text("Next hour", exact=True)).bounding_box())
        await check("sma select", p.locator("select").filter(has=p.locator("option[value='sma_cross']")).count())
        await check("Run backtest", p.get_by_role("button", name=re.compile("Run backtest")).count())

        await p.goto(lib.APP + "/bots", wait_until="networkidle")
        await check("Automation fund card", card_of(p.get_by_text("Automation fund", exact=True)).bounding_box())
        await check("Advisor pill", p.get_by_role("button", name="Advisor").count())

        await p.goto(lib.APP + "/wallet", wait_until="networkidle")
        await check("Trading P&L card", card_of(p.get_by_text("Trading P&L", exact=True)).bounding_box())
        await check("Reconciliation card", card_of(p.get_by_text("Reconciliation", exact=True).first).bounding_box())

        await p.goto(lib.APP + "/arbitrage", wait_until="networkidle")
        await check("Avg after fees card", card_of(p.get_by_text("Avg after taker fees", exact=True)).bounding_box())
        await check("Profitable windows card", card_of(p.get_by_text("Profitable windows", exact=True)).bounding_box())

        await p.goto(lib.APP + "/risk", wait_until="networkidle")
        await check("Trading loss limit card", card_of(p.get_by_text("Trading loss limit", exact=True).first).bounding_box())
        await check("Concentration card", card_of(p.get_by_text("Concentration", exact=True).first).bounding_box())
        await check("Portfolio limits card", card_of(p.get_by_text("Portfolio limits", exact=True).first).bounding_box())

        await p.goto(lib.APP + "/killswitch", wait_until="networkidle")
        await check("Engage button", p.get_by_role("button", name="Engage kill switch").count())
        await check("RAFT card", card_of(p.get_by_text("RAFT cluster", exact=False).first).bounding_box())
        await check("3 of 3 text", p.get_by_text(re.compile("3 of 3 nodes up")).count())

        await p.goto("http://localhost:5190/login", wait_until="networkidle")
        await check("5190 label", p.get_by_text(re.compile("REAL MONEY SERVER")).count())
        await ctx.close()
        await b.close()
    print("\n".join(ok))


asyncio.run(main())
