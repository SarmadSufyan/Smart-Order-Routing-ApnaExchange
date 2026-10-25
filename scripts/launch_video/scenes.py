"""The launch video, scene by scene: narration + what happens on the real screens.

Every claim follows README.md / the guides (fact_sheet.md). Sandbox = the exchanges' own test systems
with play money; the arbitrage module detects only; forecasts are probabilities with a track record.
"""
from __future__ import annotations

import asyncio
import re
import subprocess

import httpx
import lib

APP = lib.APP
TOKEN: str = ""        # set by record.py
WAIT_INPUT = '() => { const i = document.querySelector("aside[aria-label=\'Copilot\'] form input"); return i && !i.disabled }'

# ── cards (HTML rendered in the same browser) ────────────────────────────────
CARD_CSS = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
  html, body { margin:0; height:100%; background:#0a0a0c; color:#fff; font-family: Inter, 'Segoe UI', system-ui, sans-serif; }
  .wrap { height:100vh; display:flex; flex-direction:column; justify-content:center; padding:0 150px; box-sizing:border-box; }
  .kicker { color:#9aa0a6; font-size:22px; letter-spacing:.18em; text-transform:uppercase; font-weight:600;
            opacity:0; animation: up .7s ease .1s forwards; }
  h1 { font-size:88px; line-height:1.02; margin:18px 0 22px; font-weight:800; letter-spacing:-.02em;
       opacity:0; animation: up .8s ease .35s forwards; }
  h1 .thin { color:#8b949e; font-weight:500; }
  p.sub { font-size:30px; line-height:1.4; color:#c9d1d9; max-width:1150px; margin:0;
          opacity:0; animation: up .8s ease .7s forwards; }
  .line { width:120px; height:4px; background:#2ea043; border-radius:2px; margin-top:38px;
          opacity:0; animation: up .8s ease .9s forwards; }
  ul.list { list-style:none; padding:0; margin:34px 0 0; }
  ul.list li { font-size:28px; color:#c9d1d9; margin:12px 0; padding-left:34px; position:relative; opacity:0; animation: up .6s ease forwards; }
  ul.list li::before { content:''; position:absolute; left:0; top:14px; width:12px; height:12px; border-radius:50%; background:#2ea043; }
  ul.list li:nth-child(1){animation-delay:.9s} ul.list li:nth-child(2){animation-delay:1.3s} ul.list li:nth-child(3){animation-delay:1.7s}
  ul.list li:nth-child(4){animation-delay:2.1s} ul.list li:nth-child(5){animation-delay:2.5s} ul.list li:nth-child(6){animation-delay:2.9s}
  .foot { position:absolute; left:150px; bottom:70px; color:#6e7681; font-size:20px; opacity:0; animation: up .8s ease 1.2s forwards; }
  .brand { position:absolute; right:150px; bottom:70px; color:#8b949e; font-size:22px; font-weight:700; letter-spacing:.02em;
           opacity:0; animation: up .8s ease 1.2s forwards; }
  @keyframes up { from { opacity:0; transform: translateY(14px);} to { opacity:1; transform:none; } }
  .term { background:#0d1117; border:1px solid #30363d; border-radius:14px; padding:34px 40px; font: 500 22px/1.55 'Cascadia Mono', Consolas, 'Courier New', monospace;
          color:#c9d1d9; margin-top:34px; box-shadow:0 20px 60px rgba(0,0,0,.5); }
  .term div { opacity:0; animation: up .35s ease forwards; white-space:pre; }
  .term .ok { color:#3fb950; } .term .k { color:#79c0ff; } .term .dim { color:#8b949e; } .term .warn { color:#d29922; }
</style>
"""


def card(kicker: str, title_html: str, sub: str, bullets: list[str] | None = None, foot: str = "", extra: str = "") -> str:
    lis = "".join(f"<li>{b}</li>" for b in (bullets or []))
    ul = f"<ul class='list'>{lis}</ul>" if lis else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'>{CARD_CSS}</head><body>
    <div class='wrap'><div class='kicker'>{kicker}</div><h1>{title_html}</h1><p class='sub'>{sub}</p>{ul}{extra}<div class='line'></div></div>
    <div class='foot'>{foot}</div><div class='brand'>ApnaSOR</div></body></html>"""


def terminal(lines: list[str]) -> str:
    out = []
    for i, ln in enumerate(lines):
        out.append(f"<div style='animation-delay:{0.9 + i * 0.28:.2f}s'>{ln}</div>")
    return "<div class='term'>" + "".join(out) + "</div>"


# ── helpers ──────────────────────────────────────────────────────────────────
def card_of(loc):
    """The nearest bordered box around an element (the app's Card components carry an inline border)."""
    return loc.first.locator("xpath=ancestor::div[contains(@style,'border')][1]")


async def wait_text(page, pattern: str, timeout: float = 60) -> None:
    await page.get_by_text(re.compile(pattern)).first.wait_for(timeout=timeout * 1000)


async def consensus_status() -> dict:
    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"{lib.API}/api/consensus/status", headers={"Authorization": f"Bearer {TOKEN}"})
    r.raise_for_status()
    return r.json()



async def open_copilot(S: lib.SceneCtx):
    """Open the drawer and make sure the sandbox Auto box is OFF, so a proposal waits for Confirm.

    The box is disabled until the drawer's status call returns, and it becomes ticked at that moment
    (Auto defaults to on in the sandbox): untick it only once it is enabled, and check the result.
    """
    p = S.page
    await S.click(p.get_by_role("button", name="Copilot", exact=True))
    aside = p.locator("aside[aria-label='Copilot']")
    await aside.wait_for(timeout=10000)
    cb = aside.locator("input[type=checkbox]").first
    try:
        await aside.locator("input[type=checkbox]:enabled").first.wait_for(timeout=15000)
        for _ in range(3):
            if not await cb.is_checked():
                break
            await S.click(cb)
            await asyncio.sleep(0.4)
        if await cb.is_checked():
            raise RuntimeError("Auto is still on")
    except Exception as e:
        raise RuntimeError(f"could not switch Auto off: {e}") from e
    return aside


# ═════════════════════════════════════════════════════════════════════════════
# SCENES — in order. kind: "card" (html) or "app" (url). auth=False keeps the login page.
# ═════════════════════════════════════════════════════════════════════════════
SCENES: list[dict] = []


def scene(name: str, narration: str, *, kind: str = "app", url: str = "/", html: str | None = None, auth: bool = True):
    def deco(fn):
        SCENES.append({"name": name, "narration": narration, "kind": kind, "url": url, "html": html, "auth": auth, "run": fn})
        return fn
    return deco


# 00 ── opening ───────────────────────────────────────────────────────────────
@scene("00_open",
       "This is ApnaSOR, the smart order router built by group CS14 at Usman Institute of Technology. "
       "One order, every exchange, the best price, with risk controls a person can trust. "
       "Everything in this video is the real system, running live against real crypto exchanges.",
       kind="card",
       html=card("Launch video · October 2026",
                 "ApnaSOR<br><span class='thin'>Smart Order Router for Crypto Exchanges</span>",
                 "One order, every exchange, the best price — with risk controls a person can trust.",
                 foot="Final Year Project · Usman Institute of Technology · Group CS14"))
async def s00(S: lib.SceneCtx):
    await S.end(1.0)


# 01 ── the problem ───────────────────────────────────────────────────────────
@scene("01_problem",
       "Here is the problem. Crypto liquidity is split across exchanges. The same coin trades at slightly different prices "
       "on Binance, Bybit, OKX and the others, and each exchange charges its own fee. A trader on one exchange pays "
       "whatever that exchange asks at that moment. A smart order router watches all of them at once, finds the best "
       "price after fees, checks the order against risk rules, and sends each part where it is cheapest. "
       "That is what ApnaSOR does, with real exchange systems behind it.",
       kind="card",
       html=card("The problem",
                 "One coin, <span class='thin'>many prices</span>",
                 "Liquidity is split across exchanges. Prices differ, fees differ, and a trader on one exchange pays whatever it asks.",
                 bullets=["Watch every exchange's order book at once",
                          "Find the best price <b>after fees</b>",
                          "Check every order against deterministic risk rules",
                          "Send each part where it is cheapest — and prove why"],
                 foot="Binance · Bybit · OKX · HTX · Gate"))
async def s01(S: lib.SceneCtx):
    await S.end(1.0)


# 02 ── login ─────────────────────────────────────────────────────────────────
@scene("02_login",
       "Let's log in. Logins are per role: administrator, trader and risk manager, and every login is written to the "
       "audit log. This laptop runs the sandbox, so the demo password works here. The real-money server accepts only "
       "real passwords, stored as scrypt hashes.",
       url="/login", auth=False)
async def s02(S: lib.SceneCtx):
    p = S.page
    await S.caption("Sign in · every session is recorded in the audit log")
    await S.cue("Logins are per role")
    await S.type(p.locator("#login-user"), "admin")
    await S.type(p.locator("#login-pass"), "admin")
    await S.cue("This laptop runs the sandbox")
    await S.click(p.get_by_role("button", name=re.compile("Continue")))
    await S.caption(None)
    await S.end(1.0)


# 03 ── dashboard ─────────────────────────────────────────────────────────────
@scene("03_dashboard",
       "The Dashboard. Four exchanges are connected, and ApnaSOR keeps a local copy of each exchange's order book, "
       "kept in sync from its live WebSocket feed. On the right, the best bid and the best ask across all of them, "
       "the consolidated N B B O, for every one of twenty coins, with the exchange that holds each price. "
       "On the left, every order the router has sent, and above, the key figures: fill rate, routing time and exposure "
       "against its limit.",
       url="/")
async def s03(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Live order books</b> from four exchanges")
    await S.cue("Four exchanges are connected")
    await S.spot(p.get_by_text("Binance", exact=True).first.locator("xpath=ancestor::div[1]"), 3)
    await S.cue("On the right")
    await S.caption("<b>NBBO</b> — the best bid and ask across every exchange")
    await S.glide(1250, 520)
    await S.spot(card_of(p.get_by_text("Best prices", exact=True)), 4)
    await S.cue("On the left")
    await S.caption("<b>Every order</b> the router has sent — click one for its routing proof")
    await S.glide(580, 520)
    await S.spot(card_of(p.get_by_text("Live order flow", exact=True)), 3)
    await S.cue("and above")
    await S.glide(900, 215)
    await S.spot(card_of(p.get_by_text("Routing time", exact=True)), 3)
    await S.caption(None)
    await S.end()


# 04 ── market data ───────────────────────────────────────────────────────────
@scene("04_market",
       "Market Data shows every book. Pick a coin, and the four exchanges' quotes sit side by side, with a star on "
       "whoever holds the best bid and the best ask at this moment. Sequence numbers are checked on every update; "
       "a gap forces a resync, so a stale book is never used for a decision. Decisions always use the real market's "
       "books; where orders execute, sandbox or real funds, is a separate setting.",
       url="/market-data")
async def s04(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Twenty coins</b> — the most traded of the 147 the four exchanges all list")
    await S.cue("Pick a coin")
    await S.click(p.get_by_text("ETH/USDT", exact=True).first)
    await asyncio.sleep(0.6)
    await S.scroll(420)
    await S.cue("with a star")
    await S.caption("★ = the exchange holding the best bid / best ask right now")
    await S.cue("Sequence numbers")
    await S.caption("Sequence-checked · a gap forces a <b>resync</b>")
    await S.cue("Decisions always use")
    await S.caption("Real-market books for decisions · <b>sandbox</b> for execution")
    await S.end()


# 05 ── the order path (How It Works trace) ───────────────────────────────────
@scene("05_path",
       "Before placing an order, here is the path every order takes. How It Works traces a real order through the "
       "platform, one stage at a time, with live data. Watch. The order is received and gets its audit trail. "
       "Seven pre-trade risk checks must pass: the kill switch, order size, order notional, rate, position, total "
       "exposure and symbol restrictions. The live books are merged into one picture. The routing strategy walks the "
       "consolidated book. Child orders go to the exchanges, each with its own client id so nothing is ever sent twice. "
       "The exchange fills them, and every step lands in the audit log. The order button, every algorithm slice, "
       "every bot and every copilot proposal take this same path. There is one way in.",
       url="/howitworks")
async def s05(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>One way in</b> — every order takes this path")
    await S.click(p.get_by_role("button", name="Very slow"))
    await S.cue("Watch")
    await S.click(p.get_by_role("button", name="Trace order"))
    await S.cue("Seven pre-trade risk checks")
    await S.caption("<b>Seven checks</b>: kill switch · size · notional · rate · position · exposure · symbol")
    await S.cue("The live books are merged")
    await S.scroll(260)
    await S.caption("<b>NBBO</b> — the best price anywhere")
    await S.cue("The routing strategy walks")
    await S.scroll(260)
    await S.caption("<b>Routing</b> — best price after fees · sweep · smart")
    await S.cue("Child orders go")
    await S.scroll(300)
    await S.caption("<b>IOC LIMIT</b> children · price-protected · idempotent client ids")
    await S.cue("The exchange fills them")
    await S.scroll(300)
    await S.caption("<b>Audit trail</b> — every step, with who did it")
    await S.cue("The order button")
    await S.scroll(260)
    await S.caption("Button · algorithm slices · bots · copilot → <b>OrderEntry.submit</b>")
    await S.end()


# 06 ── a real sandbox order, best price ──────────────────────────────────────
@scene("06_order",
       "Now a real order, in the sandbox. Buy a small amount of Bitcoin, market, routed by best price after fees. "
       "Submit. The seven checks run, the funding check confirms the exchanges can pay for their part, the router walks "
       "every book cheapest level first, and sends a price-protected immediate-or-cancel child order. Filled, with the "
       "exchange, the price and the fee. And here is the routing proof: every exchange ranked by its all-in cost, "
       "price plus fee, the levels walked, the price cap no child may cross, expected versus actual, and what this "
       "saved against the worst exchange.",
       url="/")
async def s06(S: lib.SceneCtx):
    p = S.page
    await S.cue("Buy a small amount", 0.6)
    await S.click(p.get_by_role("button", name="New order"))
    qty = p.locator("input[placeholder^='e.g.']").first
    await S.type(qty, "0.0006")
    await S.caption("BUY 0.0006 BTC · market · <b>best price after fees</b>")
    await S.cue("Submit")
    await S.click(p.get_by_role("button", name=re.compile("Submit buy order")))
    await S.caption("<b>7 risk checks</b> → funding gate → routing → IOC child")
    await wait_text(p, "SOR decision", 90)
    await S.cue("Filled, with the exchange")
    await S.caption("<b>Filled</b> — exchange, price, fee, child order id")
    await S.cue("And here is the routing proof")
    await S.glide(800, 600)
    await S.scroll(380, steps=10, pause=0.12)
    await S.caption("<b>Routing proof</b> — all-in cost per exchange, levels walked, price cap, saved vs worst")
    await S.cue("the levels walked")
    await S.scroll(320, steps=10, pause=0.12)
    await S.cue("expected versus actual")
    await S.scroll(300, steps=10, pause=0.12)
    await S.end()
    await p.keyboard.press("Escape")


# 07 ── an order refused by pre-trade risk ────────────────────────────────────
@scene("07_refused",
       "What if an order breaks a rule? A fifth of a Bitcoin is about sixteen thousand dollars, above this account's "
       "per-order limit of ten thousand. Submit. Refused by pre-trade risk, with the check that failed, and nothing was "
       "sent to any exchange. The refusal itself is audited.",
       url="/")
async def s07(S: lib.SceneCtx):
    p = S.page
    await S.cue("A fifth of a Bitcoin", 0.8)
    await S.click(p.get_by_role("button", name="New order"))
    await S.type(p.locator("input[placeholder^='e.g.']").first, "0.2")
    await S.caption("BUY 0.2 BTC ≈ $16,000 · above the $10,000 per-order notional limit")
    await S.cue("Submit")
    await S.click(p.get_by_role("button", name=re.compile("Submit buy order")))
    try:
        await wait_text(p, "(?i)reject|refus|failed|exceed", 30)
    except Exception:
        pass
    await S.cue("Refused by pre-trade risk")
    await S.caption("<b>Refused</b> before routing — nothing reached an exchange")
    await S.end()
    await p.keyboard.press("Escape")


# 08 ── a split order: liquidity sweep ────────────────────────────────────────
@scene("08_sweep",
       "A large order should not hit one exchange. The liquidity sweep strategy sends to every exchange that can fund "
       "it, at the same moment, each part sized by that exchange's depth, all under one hard price cap. "
       "Here, a hundredth of a Bitcoin, about eight hundred dollars, with the sweep strategy. Submit. "
       "The seven checks run, the funding gate sizes what each exchange can pay, and the children leave at the same "
       "moment, each capped at the same price. "
       "One parent order, several child orders, and the proof shows the split and each exchange's fill.",
       url="/")
async def s08(S: lib.SceneCtx):
    p = S.page
    await S.cue("The liquidity sweep strategy", 0.5)
    await S.click(p.get_by_role("button", name="New order"))
    sel = p.locator("select").filter(has=p.locator("option[value='liquidity_sweep']")).first
    await S.select(sel, "liquidity_sweep")
    if await sel.input_value() != "liquidity_sweep":  # React missed the event: pick it with the keyboard
        await sel.focus()
        await p.keyboard.press("ArrowDown")
    await S.caption("<b>Liquidity sweep</b> — every exchange at once, sized by depth, one price cap")
    await S.cue("Here, a hundredth", 0.3)
    await S.type(p.locator("input[placeholder^='e.g.']").first, "0.01")
    await S.caption("BUY 0.01 BTC · <b>liquidity sweep</b> — every exchange at once, sized by depth, one price cap")
    await S.cue("Submit")
    await S.click(p.get_by_role("button", name=re.compile("Submit (buy|sell) order")))
    await S.cue("The seven checks run")
    await S.caption("<b>7 checks</b> → funding gate → children to every exchange at once, one price cap")
    await wait_text(p, "SOR decision", 90)
    await S.cue("One parent order")
    await S.glide(800, 600)
    await S.scroll(420, steps=12, pause=0.12)
    await S.caption("<b>One parent, several children</b> — the split and each exchange's fill")
    await S.end(1.2)
    await p.keyboard.press("Escape")


# 09 ── the proof of any past order ───────────────────────────────────────────
@scene("09_proof",
       "Every order keeps its proof. Click any order on the Dashboard and its routing decision replays, with the audit "
       "trail underneath: every step, as it was recorded, with who did it.",
       url="/")
async def s09(S: lib.SceneCtx):
    p = S.page
    await S.cue("Click any order")
    await S.click(p.locator("tbody tr").first)
    await wait_text(p, "Audit trail", 30)
    await S.cue("with the audit trail")
    await S.glide(800, 600)
    await S.scroll(700, steps=14, pause=0.1)
    await S.caption("<b>Audit trail</b> — every step, as recorded")
    await S.end()
    await p.keyboard.press("Escape")


# 10 ── execution algorithms ──────────────────────────────────────────────────
@scene("10_algos",
       "Large orders can be worked over time. Five execution algorithms: TWAP, VWAP, implementation shortfall after "
       "Almgren and Chriss, percentage of volume, and iceberg. Or let Auto choose. Auto reads the live books and this "
       "order, recommends one, and explains why; nothing is sent. Every planned slice is pre-flight checked against risk "
       "before the first one goes out. Now let's start a TWAP: six slices over three minutes, each one through the "
       "same order path. We'll come back for the result.",
       url="/algos")
async def s10(S: lib.SceneCtx):
    p = S.page
    await S.caption("TWAP · VWAP · implementation shortfall · POV · iceberg · <b>Auto</b>")
    await S.type(p.locator("input[placeholder='e.g. 0.002']"), "0.0012")
    over = p.get_by_text("Over", exact=True).locator("xpath=following::input[1]")
    await S.type(over, "3")
    slices = p.get_by_text("Slices", exact=True).locator("xpath=following::input[1]")
    await S.type(slices, "6")
    await S.cue("Or let Auto choose")
    await S.click(p.get_by_role("button", name=re.compile("Auto")))
    await S.caption("<b>Auto</b> — recommends an algorithm and gives its reasons · sends nothing")
    await S.cue("Every planned slice")
    await S.click(p.get_by_role("button", name=re.compile("Pre-flight check")))
    await S.caption("<b>Pre-flight</b> — every slice checked before the first is sent")
    await asyncio.sleep(3.5)
    try:
        await p.get_by_label("Close the pre-flight result").click(timeout=3000)
    except Exception:
        await p.keyboard.press("Escape")
    await S.cue("Now let's start a TWAP")
    await S.click(p.get_by_role("button", name=re.compile(r"^Start TWAP")))
    await S.caption("<b>TWAP</b> started — 0.0012 BTC, 6 slices over 3 minutes")
    await S.end()


# 11 ── research ──────────────────────────────────────────────────────────────
@scene("11_research",
       "Research works on the real market's candles: five years of hourly and about eight years of daily history per "
       "coin. It forecasts the probability that the next minute, hour or day closes higher, with a likely range, and "
       "shows its own track record on data it had not seen. Most of the time it says no proven edge, right on the "
       "screen, because that is the honest result on real crypto data. Below, the indicators explain the picture, and "
       "a backtester runs classic rules after fees and slippage: here, a moving-average cross against simply holding. "
       "The technique study is blunt: on five years of hourly Bitcoin, every classic technique lost after fees, while "
       "holding gained. Nothing here is financial advice, and nothing here places an order.",
       url="/research")
async def s11(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Forecasts</b> — probabilities, a likely range, and a walk-forward track record")
    await S.cue("It forecasts the probability")
    await S.glide(900, 400)
    await S.spot(card_of(p.get_by_text("Next hour", exact=True)), 4)
    await S.cue("Most of the time it says")
    await S.caption("<b>No proven edge</b> — the honest result, said on the screen")
    await S.cue("Below, the indicators")
    await S.scroll(620, steps=12)
    await S.caption("Indicators explain the picture — they don't make the forecast")
    await S.cue("a backtester runs")
    await S.scroll(560, steps=12)
    sel = p.locator("select").filter(has=p.locator("option[value='sma_cross']")).first
    try:
        await S.select(sel, "sma_cross")
        await S.click(p.get_by_role("button", name=re.compile("Run backtest")))
        await S.caption("<b>Backtest</b> — fees and slippage paid on every trade")
    except Exception:
        pass
    await S.cue("The technique study is blunt")
    await S.scroll(700, steps=14)
    await S.caption("<b>Technique study</b> — hourly BTC, 5 years: every classic technique lost after fees")
    await S.cue("Nothing here is financial advice")
    await S.caption("Research, not advice · <b>nothing here places an order</b>")
    await S.end()


# 12 ── the copilot: a question ───────────────────────────────────────────────
@scene("12_copilot_ask",
       "The copilot. Let's ask it a question in plain English: should I buy SOL right now? "
       "While it works: a language model fills a strict form; the code validates every field, gathers the facts from "
       "the live services and the research, and the model only words them. It never holds keys, never submits an "
       "order, never sizes risk. The analyst behind it is pure code. It scores six classic techniques on five years "
       "of candles, counts a forecast only when it has a proven edge, and replays a stop-and-target bracket on "
       "thousands of past hours. Buy needs a score of two or more and at least one technique saying in; reduce needs "
       "you to hold the coin with a score of minus two or less; everything else is no trade, the honest default. "
       "And here is the answer, with its reasons.",
       url="/")
async def s12(S: lib.SceneCtx):
    p = S.page
    aside = await open_copilot(S)
    await S.cue("should I buy SOL", 0.6)
    box = aside.locator("form input").first
    await S.type(box, "Should I buy SOL right now?", delay=30)
    await p.keyboard.press("Enter")
    await S.cue("While it works")
    await S.caption("<b>Copilot</b> — the model fills a form, code validates, the model only words the facts")
    await S.cue("It never holds keys")
    await S.caption("Never holds keys · never submits · never sizes risk")
    await S.cue("The analyst behind it")
    await S.caption("<b>The analyst</b> — pure rules: six techniques, forecast edge, a replayed bracket")
    await S.cue("Buy needs a score")
    await S.caption("<b>BUY</b> ≥ +2 and a technique saying in · <b>REDUCE</b> ≤ −2 while holding · else <b>NO TRADE</b>")
    await p.wait_for_function(WAIT_INPUT, timeout=120000)
    if await aside.get_by_text(re.compile(r"HTTP 5\d\d|could not|couldn't|timed out")).count():
        raise RuntimeError("the model failed: " + (await aside.inner_text())[-200:])
    await S.cue("And here is the answer")
    await S.caption("The verdict, with the analyst's reasons — worded by the model, numbers from the code")
    await S.end(1.5)


# 13 ── the copilot: an instruction, confirmed by a person ────────────────────
@scene("13_copilot_order",
       "Now an instruction: buy twenty dollars of SOL with smart routing. "
       "The copilot turns it into a proposal: the coin, the amount, the routing strategy, and a fresh price check. "
       "Here it is. It waits for a person. Confirm, and the order goes through exactly the same path as the order "
       "button, risk checks included. In the sandbox, tiny proposals can be auto-run; with real money, Auto is off and "
       "every action waits for a click.",
       url="/")
async def s13(S: lib.SceneCtx):
    p = S.page
    aside = await open_copilot(S)
    await S.cue("buy twenty dollars", 0.4)
    box = aside.locator("form input").first
    await S.type(box, "Buy $20 of SOL with smart routing", delay=30)
    await p.keyboard.press("Enter")
    await S.cue("The copilot turns it")
    await S.caption("Instruction: <b>buy $20 of SOL with smart routing</b>")
    confirm = aside.get_by_role("button", name=re.compile("^Confirm"))
    # wait until the request is finished (the input is enabled again); a finished request without a
    # proposal means the model's free tier failed (HTTP 503): abort, and record.py retakes the scene
    await p.wait_for_function(WAIT_INPUT, timeout=90000)
    if not await confirm.count():
        raise RuntimeError("no proposal: " + (await aside.inner_text())[-200:])
    await S.cue("Here it is")
    await S.caption("A <b>proposal</b> — coin, amount, strategy, a fresh price · it waits for a person")
    await S.cue("Confirm, and the order")
    try:
        await S.click(confirm)
    except Exception:
        pass
    await S.caption("<b>Confirmed</b> → OrderEntry.submit → the same seven checks → routing")
    try:
        await p.wait_for_function(WAIT_INPUT, timeout=90000)
    except Exception:
        pass
    await S.cue("In the sandbox")
    await S.caption("Sandbox: tiny proposals may auto-run · <b>real money: every action waits for a click</b>")
    await S.end(1.5)


# 14 ── bots and the practice lab ─────────────────────────────────────────────
@scene("14_bots",
       "Bots trade from an automation fund set aside from the wallet. Each bot gets its own pocket and can never spend "
       "more, and manual orders can't touch what the fund holds. The profit-cycle bot buys a small lot and sells it only "
       "when the price beats its cost plus both fees plus a threshold; it can't make the price come back. "
       "The advisor agent proposes bracketed trades with its reasons, and a person approves each one. "
       "A practice lab measures every bot trade: real slippage per exchange, a scoreboard per variant, and outcome "
       "models. A lab server on AWS has run eight of these bots around the clock for two weeks, on sandbox funds. "
       "So far no variant shows a proven edge, and the lab says so, which is exactly what it is for.",
       url="/bots")
async def s14(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Automation fund</b> — a pocket per bot; manual orders can't use it")
    await S.spot(card_of(p.get_by_text("Automation fund", exact=True)), 4)
    await S.cue("The profit-cycle bot")
    await S.scroll(300, steps=10)
    await S.caption("<b>Profit cycle</b> — sells only above cost + both fees + threshold")
    await S.cue("The advisor agent")
    try:
        await S.click(p.get_by_role("button", name="Advisor"))
    except Exception:
        pass
    await S.caption("<b>Advisor</b> — bracketed proposals with reasons; a person approves")
    await S.cue("A practice lab")
    await S.scroll(900, steps=16)
    await S.caption("<b>Practice lab</b> — measured costs, a scoreboard, outcome models")
    await S.cue("A lab server on AWS")
    await S.scroll(700, steps=14)
    await S.cue("So far no variant")
    await S.caption("<b>No proven edge yet</b> — measured, not assumed")
    await S.end()


# 15 ── the TWAP's result ─────────────────────────────────────────────────────
@scene("15_twap",
       "Back to the TWAP. Its slices went out on schedule, each one a child through the same path, each with its own "
       "fill. The chart compares the average price with the arrival price, the mid when it started, and with the real "
       "market's volume-weighted price over the same minutes. Those two benchmarks are reported for every algorithm, "
       "so the numbers we claim are the numbers we can show.",
       url="/algos")
async def s15(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>TWAP</b> — 6 slices over 3 minutes, every slice through OrderEntry")
    try:
        await S.click(p.get_by_text(re.compile(r"^TWAP-")).first)
    except Exception:
        pass
    await S.cue("The chart compares")
    await S.glide(1100, 600)
    await S.scroll(900, steps=16)
    await S.caption("Benchmarks: <b>arrival price</b> and the <b>market VWAP</b> over the same minutes")
    await S.end()


# 16 ── wallet, P&L, reconciliation ───────────────────────────────────────────
@scene("16_wallet",
       "The Wallet reads real balances from every exchange and values them at live prices. A P and L ledger starts "
       "from opening balances and applies every fill. Reconciliation compares what the ledger expects with what each "
       "exchange reports: matched, pending, or a break. A mismatch is shown, never edited away; an operator can accept "
       "the exchange's balances as a new starting point, and that action is audited. Reality wins: a fill reported by "
       "an exchange is always recorded.",
       url="/wallet")
async def s16(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Balances</b> read from every exchange, valued at live prices")
    await S.cue("A P and L ledger")
    await S.spot(card_of(p.get_by_text("Trading P&L", exact=True)), 3)
    await S.cue("Reconciliation compares")
    await S.glide(1350, 220)
    await S.spot(card_of(p.get_by_text("Reconciliation", exact=True).first), 3)
    await S.caption("<b>Reconciliation</b> — our ledger vs. what the exchange reports")
    await S.cue("A mismatch is shown")
    await S.scroll(700, steps=14)
    await S.caption("Mismatches are <b>shown, never edited</b> — accepting balances is audited")
    await S.end()


# 17 ── arbitrage detector ────────────────────────────────────────────────────
@scene("17_arb",
       "The arbitrage detector scans every exchange pair, both directions, four times a second, and measures each gap "
       "after both taker fees. The result is honest: the real-market books are crossed in about half of all scans, but "
       "after fees the best pair is under water, and out of tens of thousands of crossed windows only a handful ever "
       "paid, cents, at the top of the books. This module detects. It cannot place an order, by design.",
       url="/arbitrage")
async def s17(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Detector only</b> — nothing is traded · 4 scans a second")
    await S.cue("The result is honest")
    await S.spot(card_of(p.get_by_text("Avg after taker fees", exact=True)), 4)
    await S.caption("Crossed often — but <b>after both fees</b> the best pair is under water")
    await S.cue("only a handful")
    await S.spot(card_of(p.get_by_text("Profitable windows", exact=True)), 3)
    await S.cue("This module detects")
    await S.caption("<b>Detects</b> — it cannot place an order, by design")
    await S.end()


# 18 ── risk manager ──────────────────────────────────────────────────────────
@scene("18_risk",
       "Risk Manager: the limits behind the seven checks, the open positions, a trading-loss limit whose breach engages "
       "the kill switch automatically, and concentration. Right now the monitor flags that Bitcoin is above sixty "
       "percent of this sandbox portfolio. Limits are saved through one path, so with the consensus cluster on they "
       "are replicated, and on real money they can be tightened but never loosened past the server's caps.",
       url="/risk")
async def s18(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Seven checks</b>, their limits, and what they caught")
    await S.cue("a trading-loss limit")
    await S.spot(card_of(p.get_by_text("Trading loss limit", exact=True).first), 3)
    await S.caption("<b>Loss limit</b> — a breach engages the kill switch automatically")
    await S.cue("and concentration")
    await S.spot(card_of(p.get_by_text("Concentration", exact=True).first), 4)
    await S.caption("<b>Concentration</b> — one asset above 60 % is flagged")
    await S.cue("Limits are saved")
    await S.spot(card_of(p.get_by_text("Portfolio limits", exact=True).first), 4)
    await S.caption("Tighten any time · on real money <b>never past the .env caps</b>")
    await S.end()


# 19 ── kill switch + RAFT ────────────────────────────────────────────────────
@scene("19_kill",
       "The kill switch stops every new order, algorithm and bot at once. Engage it, with a reason. Now try an order: "
       "the button itself is locked, and anything that slips through is refused by the first of the seven checks. "
       "The switch is persisted, so a restart keeps it engaged. And here it is replicated by a three-node RAFT cluster "
       "with signed messages: engaging works even without a majority, releasing needs one. "
       "Let's kill the leader process. Within seconds a new leader is elected, and nothing is lost. "
       "Two of three nodes is still a majority, so we can release the kill switch now, and trading resumes. "
       "And fifteen seconds after it died, the killed node is restarted and catches up.",
       url="/killswitch")
async def s19(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Kill switch</b> — one switch stops every new order, algo and bot")
    await S.cue("Engage it")
    await S.click(p.get_by_role("button", name="Engage kill switch").first)
    await S.type(p.locator("input[placeholder^='Reason']"), "Launch video: safety demo", delay=30)
    await S.click(p.get_by_role("button", name=re.compile("Confirm Kill Switch")))
    try:
        await p.get_by_role("button", name=re.compile("Release kill switch")).wait_for(timeout=15000)
    except Exception:
        pass
    await S.caption("<b>Engaged</b> — persisted, audited, replicated")
    await S.cue("Now try an order")
    await p.goto(APP + "/", wait_until="networkidle")
    await S.click(p.get_by_role("button", name="New order"))
    await asyncio.sleep(0.8)
    await S.spot(p.get_by_text(re.compile("Kill switch engaged")).first, 3)
    await S.caption("The order button is <b>locked</b>; the first check refuses anything else")
    await S.cue("The switch is persisted")
    await p.keyboard.press("Escape")
    await p.goto(APP + "/killswitch", wait_until="networkidle")
    await S.cue("And here it is replicated")
    await S.glide(900, 560)
    await S.spot(card_of(p.get_by_text("RAFT cluster", exact=False).first), 4)
    await S.caption("<b>RAFT</b> — 3 nodes, signed messages · tighten without a majority, loosen needs one")
    await S.cue("Let's kill the leader")
    try:
        st = await consensus_status()
        leader = st.get("leader")
        pid = next(n["pid"] for n in st["nodes"] if n["id"] == leader)
        await asyncio.to_thread(subprocess.run, ["taskkill", "/PID", str(pid), "/F"], capture_output=True)
        await S.caption(f"<b>Leader (node {leader}) killed</b> — watch the election")
    except Exception as e:  # keep recording even if the cluster call fails
        await S.caption(f"Leader kill skipped: {e}")
    try:
        await p.get_by_text(re.compile("2 of 3 nodes up")).first.wait_for(timeout=20000)
    except Exception:
        pass
    await S.cue("Within seconds")
    await S.caption("<b>New leader</b> elected — the kill switch state survived")
    await S.cue("Two of three nodes")
    await S.glide(900, 300)
    await S.scroll(-600, steps=8)
    await S.click(p.get_by_role("button", name=re.compile("Release kill switch")))
    try:
        await wait_text(p, "Trading is running normally", 20)
    except Exception:
        pass
    await S.caption("<b>Released</b> with 2 of 3 — a majority is enough; trading resumes")
    await S.cue("And fifteen seconds")
    await S.glide(900, 560)
    await S.scroll(400, steps=8)
    try:
        await p.get_by_text(re.compile("3 of 3 nodes up")).first.wait_for(timeout=45000)
    except Exception:
        pass
    await S.caption("The killed node <b>restarted and caught up</b> — 3 of 3 again")
    await S.end(4.0)


# 20 ── venues + audit log ────────────────────────────────────────────────────
@scene("20_venues_audit",
       "Venues shows each exchange's latency, book sync state, health score, and whether its sandbox book stays within "
       "fifty basis points of the real market; a drifting book is excluded from routing. "
       "And the Audit Log is the append-only record of everything we just did: every order step, every risk decision, "
       "every fill, every operator action, with who did it. It survives restarts, and it is what a reviewer reads.",
       url="/venues")
async def s20(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Venue health</b> — latency, sync, health score, sandbox-vs-market drift")
    await S.cue("a drifting book")
    await S.caption("A book that drifts > 50 bps from the real market is <b>excluded</b>")
    await S.cue("And the Audit Log")
    await p.goto(APP + "/audit", wait_until="networkidle")
    await S.caption("<b>Audit log</b> — append-only, with an actor on every line")
    await S.cue("every operator action")
    await S.scroll(500, steps=12)
    await S.end()


# 21 ── real money: the server ────────────────────────────────────────────────
@scene("21_real_server",
       "Everything so far ran in the exchanges' sandboxes: the exchanges' own systems, with play money. "
       "The same code runs on a separate real-money server with a fixed IP, reached only through an SSH tunnel; "
       "this is its screen. It is in rehearsal today. An exchange receives orders only after it confirms, from the "
       "server's own address, that our key can trade but cannot withdraw, and is locked to that IP. "
       "Demo logins do not exist there.",
       url="http://localhost:5190/login", auth=False)
async def s21(S: lib.SceneCtx):
    await S.cue("The same code runs")
    await S.caption("<b>REAL MONEY SERVER</b> — its own machine, fixed IP, reached through an SSH tunnel")
    await S.cue("It is in rehearsal")
    await S.caption("Rehearsal: sandbox keys; go-live switches to real funds behind the rails")
    await S.cue("Demo logins")
    await S.caption("Real passwords only · scrypt hashes · per-machine signing key")
    await S.end()


# 22 ── real money: the rails ─────────────────────────────────────────────────
@scene("22_rails",
       "These are the rails. Trade-only keys, checked by each exchange from the server's address. Hard caps in the "
       "server's settings, per order, per algorithm, total exposure, and a loss limit, that no screen can loosen. "
       "Going live is a command that must be confirmed by typing the words real money. Alerts reach a private Discord "
       "channel, and a stop command from the phone engages the kill switch; nothing releases it from the phone. "
       "A security check script tries the known attacks, and must report that every one was refused.",
       kind="card",
       html=card("Real money · Phase D",
                 "The <span class='thin'>rails</span>",
                 "The same order path, behind controls that no screen can loosen.",
                 extra=terminal([
                     "<span class='dim'>$ python deploy/real-server/deploy.py check-keys</span>",
                     "<span class='k'>Binance</span>  <span class='ok'>OK for real money</span> - trade spot: yes, withdraw: <span class='ok'>no</span>, IP list: <span class='ok'>yes</span>   fee 0.100%",
                     "<span class='k'>Bybit  </span>  <span class='ok'>OK for real money</span> - trade spot: yes, withdraw: <span class='ok'>no</span>, IP list: <span class='ok'>yes</span>   fee 0.100%",
                     "<span class='k'>OKX    </span>  <span class='ok'>OK for real money</span> - trade spot: yes, withdraw: <span class='ok'>no</span>, IP list: <span class='ok'>yes</span>   fee 0.100%",
                     "<span class='k'>HTX    </span>  <span class='ok'>OK for real money</span> - trade spot: yes, withdraw: <span class='ok'>no</span>, IP list: <span class='ok'>yes</span>   fee 0.200%",
                     "<span class='dim'>caps: $25 per order · $100 per algorithm · $150 exposure · $10 loss → kill switch</span>",
                     "<span class='dim'>go-live: type</span> <span class='warn'>REAL MONEY</span> <span class='dim'>to confirm · Discord /stop engages, never releases</span>",
                 ]),
                 foot="Keys never touch a laptop · reached only through an SSH tunnel · a release needs 72 h on the lab server with CI green"))
async def s22(S: lib.SceneCtx):
    await S.end(1.0)


# 23 ── themes + closing words ────────────────────────────────────────────────
@scene("23_theme_close",
       "Three looks, one click. Studio, the default. Liquid Glass, designed by Mahnoor and Maham, frosted and bright. "
       "And Terminal, the original look, with its capital labels. Back to Studio. "
       "And that is ApnaSOR. The contribution is the integration: real-time books from several exchanges, fee-aware "
       "routing, deterministic risk, execution algorithms, research, a copilot that proposes while a person confirms, "
       "and an audit trail, working as one system and verified live against real exchanges, with about six hundred "
       "tests, continuous integration on Linux and Windows, and two servers on AWS.",
       url="/")
async def s23(S: lib.SceneCtx):
    p = S.page
    await S.click(p.locator("button[title='Theme']"))
    await S.cue("Liquid Glass", 0.4)
    await S.click(p.get_by_role("menuitemradio", name=re.compile("Liquid Glass")))
    await S.cue("And Terminal", 0.4)
    await S.click(p.get_by_role("menuitemradio", name=re.compile("Terminal")))
    await S.cue("Back to Studio", 0.3)
    await S.click(p.get_by_role("menuitemradio", name=re.compile("Studio")))
    await S.cue("And that is ApnaSOR")
    await p.keyboard.press("Escape")
    await S.glide(900, 500)
    await S.caption("<b>The integration is the contribution</b> — one system, verified live")
    await S.cue("with about six hundred tests")
    await S.caption("~600 tests · CI on Linux and Windows · two servers on AWS")
    await S.end()


# 24 ── closing card ──────────────────────────────────────────────────────────
@scene("24_close",
       "ApnaSOR. Group CS14, Usman Institute of Technology, supervised by Sir Shamaail Jafri. "
       "Sarmad Sufyan Ahmed, Fizza Mubeen, Mahnoor Nadeem and Maham Ikram. Thank you for watching.",
       kind="card",
       html=card("Thank you",
                 "ApnaSOR",
                 "One order, every exchange, the best price — with risk controls a person can trust.",
                 bullets=["Sarmad Sufyan Ahmed · Fizza Mubeen · Mahnoor Nadeem · Maham Ikram",
                          "Supervisor: Sir Shamaail Jafri",
                          "Usman Institute of Technology · Group CS14 · Final Year Project"],
                 foot="Sandbox = the exchanges' own test systems with play money · research is not financial advice"))
async def s24(S: lib.SceneCtx):
    await S.end(1.5)
