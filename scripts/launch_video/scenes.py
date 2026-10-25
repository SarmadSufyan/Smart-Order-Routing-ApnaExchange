"""The launch video, scene by scene: narration + what happens on the real screens (Studio, light mode).

Under eight minutes: an introduction with a worked example (who it is for, what it does), then the main
parts on the live sandbox. Every claim follows README.md / the guides: sandbox = the exchanges' own test
systems with play money; the arbitrage module detects only; forecasts are probabilities with a track record.
"""
from __future__ import annotations

import asyncio
import re
import subprocess

import httpx

import lib

APP = lib.APP
TOKEN: str = ""        # set by record.py
WAIT_INPUT = "() => { const i = document.querySelector(\"aside[aria-label='Copilot'] form input\"); return i && !i.disabled }"

# ── cards (HTML rendered in the same browser; light, like the app) ───────────
CARD_CSS = """
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
  html, body { margin:0; height:100%; background:#F6F6F7; color:#111114; font-family: Inter, 'Segoe UI', system-ui, sans-serif; }
  .wrap { height:100vh; display:flex; flex-direction:column; justify-content:center; padding:0 150px; box-sizing:border-box; }
  .kicker { color:#6B7280; font-size:22px; letter-spacing:.18em; text-transform:uppercase; font-weight:600;
            opacity:0; animation: up .7s ease .1s forwards; }
  h1 { font-size:84px; line-height:1.04; margin:18px 0 22px; font-weight:800; letter-spacing:-.02em;
       opacity:0; animation: up .8s ease .35s forwards; }
  h1 .thin { color:#8A8F98; font-weight:500; }
  p.sub { font-size:30px; line-height:1.4; color:#3C4043; max-width:1200px; margin:0;
          opacity:0; animation: up .8s ease .7s forwards; }
  .line { width:120px; height:4px; background:#1F8A3A; border-radius:2px; margin-top:38px;
          opacity:0; animation: up .8s ease .9s forwards; }
  ul.list { list-style:none; padding:0; margin:30px 0 0; }
  ul.list li { font-size:27px; color:#3C4043; margin:11px 0; padding-left:34px; position:relative; opacity:0; animation: up .6s ease forwards; }
  ul.list li::before { content:''; position:absolute; left:0; top:13px; width:12px; height:12px; border-radius:50%; background:#1F8A3A; }
  ul.list li b { color:#111114; }
  ul.list li:nth-child(1){animation-delay:.9s} ul.list li:nth-child(2){animation-delay:1.3s} ul.list li:nth-child(3){animation-delay:1.7s}
  ul.list li:nth-child(4){animation-delay:2.1s} ul.list li:nth-child(5){animation-delay:2.5s} ul.list li:nth-child(6){animation-delay:2.9s}
  ul.list li:nth-child(7){animation-delay:3.3s}
  .foot { position:absolute; left:150px; bottom:70px; color:#8A8F98; font-size:20px; opacity:0; animation: up .8s ease 1.2s forwards; }
  .brand { position:absolute; right:150px; bottom:70px; color:#6B7280; font-size:22px; font-weight:700; letter-spacing:.02em;
           opacity:0; animation: up .8s ease 1.2s forwards; }
  @keyframes up { from { opacity:0; transform: translateY(14px);} to { opacity:1; transform:none; } }

  /* the worked example: four exchange tiles revealed step by step (body.s1 … body.s6, set from the narration) */
  .ex { margin-top:26px; }
  .tiles { display:grid; grid-template-columns: repeat(4, 1fr); gap:22px; }
  .tile { background:#fff; border:1px solid #E3E4E8; border-radius:18px; padding:22px 26px; box-shadow:0 10px 30px rgba(0,0,0,.06);
          opacity:0; transform:translateY(14px); transition: all .5s ease; position:relative; }
  body.s1 .tile { opacity:1; transform:none; }
  .tile .name { font-size:24px; font-weight:700; color:#111114; }
  .tile .ask { font-size:40px; font-weight:800; letter-spacing:-.02em; margin-top:8px; }
  .tile .lbl { font-size:16px; color:#8A8F98; letter-spacing:.08em; text-transform:uppercase; margin-top:14px; }
  .tile .fee, .tile .allin { font-size:28px; font-weight:700; opacity:0; transition: opacity .5s ease; }
  .tile .fee { color:#B42318; }
  body.s2 .tile .fee, body.s2 .tile .lbl.f { opacity:1; }
  .tile .lbl { opacity:0; transition: opacity .5s ease; }
  body.s3 .tile .allin, body.s3 .tile .lbl.a { opacity:1; }
  .badge { position:absolute; top:-14px; right:18px; background:#111114; color:#fff; font-size:16px; font-weight:700;
           padding:6px 12px; border-radius:999px; opacity:0; transition: opacity .4s ease; }
  body.s2 .badge.looks { opacity:1; }
  body.s3 .badge.looks { opacity:0; }
  .badge.best { background:#1F8A3A; }
  body.s3 .badge.best { opacity:1; }
  body.s3 .tile.win { border-color:#1F8A3A; box-shadow:0 0 0 4px rgba(31,138,58,.15), 0 14px 34px rgba(0,0,0,.08); }
  .rank { position:absolute; left:18px; top:-16px; width:34px; height:34px; border-radius:50%; background:#F6F6F7; border:1px solid #E3E4E8;
          font-weight:800; font-size:18px; display:flex; align-items:center; justify-content:center; opacity:0; transition: opacity .4s ease; }
  body.s3 .rank { opacity:1; }
  .note { font-size:28px; color:#3C4043; margin-top:26px; opacity:0; transition: opacity .5s ease; }
  .note b { color:#111114; }
  body.s4 .note.diff { opacity:1; }
  .checks { display:flex; gap:12px; flex-wrap:wrap; margin-top:22px; opacity:0; transition: opacity .5s ease; }
  body.s5 .checks { opacity:1; }
  .chk { background:#fff; border:1px solid #E3E4E8; border-radius:999px; padding:9px 16px; font-size:21px; font-weight:600; color:#111114; }
  .chk::before { content:'✓ '; color:#1F8A3A; }
</style>
"""


def card(kicker: str, title_html: str, sub: str, bullets: list[str] | None = None, foot: str = "", extra: str = "") -> str:
    lis = "".join(f"<li>{b}</li>" for b in (bullets or []))
    ul = f"<ul class='list'>{lis}</ul>" if lis else ""
    return f"""<!doctype html><html><head><meta charset='utf-8'>{CARD_CSS}</head><body>
    <div class='wrap'><div class='kicker'>{kicker}</div><h1>{title_html}</h1><p class='sub'>{sub}</p>{ul}{extra}<div class='line'></div></div>
    <div class='foot'>{foot}</div><div class='brand'>ApnaSOR</div></body></html>"""


def tile(name: str, ask: str, fee: str, allin: str, rank: int, win: bool = False, looks: bool = False) -> str:
    return (f"<div class='tile{' win' if win else ''}'><div class='rank'>{rank}</div>"
            f"{'<div class=\"badge looks\">looks cheapest</div>' if looks else ''}"
            f"{'<div class=\"badge best\">cheapest after fees</div>' if win else ''}"
            f"<div class='name'>{name}</div><div class='ask'>{ask}</div>"
            f"<div class='lbl f'>taker fee</div><div class='fee'>{fee}</div>"
            f"<div class='lbl a'>all-in, after the fee</div><div class='allin'>{allin}</div></div>")


EXAMPLE = ("<div class='ex'><div class='tiles'>"
           + tile("Binance", "$80,700", "0.10 %", "$80,780.70", 2)
           + tile("Bybit", "$80,690", "0.10 %", "$80,770.69", 1, win=True)
           + tile("OKX", "$80,685", "0.15 %", "$80,806.03", 3, looks=True)
           + tile("Gate", "$80,695", "0.20 %", "$80,856.39", 4)
           + "</div>"
           "<div class='note diff'>After fees <b>Bybit</b> is cheapest and the exchange that <b>looked</b> cheapest is third — "
           "<b>about $35</b> apart on one Bitcoin. Every order, every book, in milliseconds.</div>"
           "<div class='checks'><span class='chk'>kill switch</span><span class='chk'>order size</span><span class='chk'>order value</span>"
           "<span class='chk'>rate</span><span class='chk'>position</span><span class='chk'>total exposure</span><span class='chk'>symbol</span>"
           "<span class='chk'>funding</span></div></div>")


# ── helpers ──────────────────────────────────────────────────────────────────
def card_of(loc):
    """The nearest bordered box around an element (the app's Card components carry an inline border)."""
    return loc.first.locator("xpath=ancestor::div[contains(@style,'border')][1]")


async def wait_text(page, pattern: str, timeout: float = 60) -> None:
    await page.get_by_text(re.compile(pattern)).first.wait_for(timeout=timeout * 1000)


async def step(S: lib.SceneCtx, cls: str) -> None:
    """Reveal the next part of a card (body class)."""
    await S.page.evaluate("(c) => document.body.classList.add(c)", cls)


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


# ── PART 1: the introduction ─────────────────────────────────────────────────
@scene("00_open",
       "ApnaSOR. A smart order router for crypto exchanges, built by group CS14 at Usman Institute of Technology.",
       kind="card",
       html=card("Launch video · October 2026",
                 "ApnaSOR<br><span class='thin'>Smart Order Router for Crypto Exchanges</span>",
                 "One order, every exchange, the best price — with risk controls a person can trust.",
                 foot="Final Year Project · Usman Institute of Technology · Group CS14"))
async def s00(S: lib.SceneCtx):
    await S.end(0.8)


@scene("01_who",
       "Who is it for? People who place crypto orders and care about the price they get: a trader working an order, "
       "the risk manager who must be sure nothing goes wrong, and the administrator who runs the desk. ApnaSOR is the "
       "order router of Apna Exchange: its users' orders go to the best real exchange behind it. The problem: the same "
       "coin trades on Binance, Bybit, OKX and HTX at once, each with its own price, its own fee and its own order "
       "book, and a person with four tabs open is guessing.",
       kind="card",
       html=card("Who it is for",
                 "One coin, <span class='thin'>four exchanges, four prices</span>",
                 "A trader, a risk manager and an administrator — and behind them, the real exchanges.",
                 bullets=["<b>Trader</b> — places the order, gets the best price after fees, sees the proof",
                          "<b>Risk manager</b> — sets the limits, holds the kill switch, reads the audit log",
                          "<b>Administrator</b> — runs the desk, the exchanges' keys, the servers",
                          "The same coin trades on <b>Binance, Bybit, OKX and HTX</b> at once — different prices, different fees"],
                 foot="ApnaSOR is the order router of Apna Exchange: its users' orders go to the best real exchange"))
async def s01(S: lib.SceneCtx):
    await S.end(0.8)


@scene("02_example",
       "An example. A trader wants to buy one Bitcoin. Binance asks eighty thousand seven hundred dollars, Bybit six "
       "hundred ninety, OKX six hundred eighty-five, Gate six hundred ninety-five. OKX looks cheapest. But each exchange "
       "charges a taker fee: a tenth of a percent on Binance and Bybit, more on OKX, double on Gate. After fees, Bybit "
       "is cheapest, and the one that looked cheapest is third, about thirty-five dollars apart on one Bitcoin. ApnaSOR "
       "does this on every order, on live books, in milliseconds, and takes the rest from the next cheapest level "
       "anywhere when one exchange runs thin. And before anything is sent, every order passes seven risk checks and a "
       "funding check, so a fat finger never reaches an exchange.",
       kind="card",
       html=card("An example", "Buy <span class='thin'>one Bitcoin</span>",
                 "Four exchanges, four asks — which one is really the cheapest?", extra=EXAMPLE,
                 foot="Illustration with round numbers; the video's orders below use the live books"))
async def s02(S: lib.SceneCtx):
    await S.cue("Binance asks")
    await step(S, "s1")
    await S.cue("But each exchange charges")
    await step(S, "s2")
    await S.cue("After fees, Bybit")
    await step(S, "s3")
    await S.cue("about thirty-five dollars")
    await step(S, "s4")
    await S.cue("every order passes seven")
    await step(S, "s5")
    await S.end(0.8)


@scene("03_what",
       "In one system: live order books from the real exchanges; the best price after fees, with a proof for every "
       "decision; seven risk checks and a kill switch; algorithms that work large orders over time; research and a "
       "copilot that proposes while a person confirms; a wallet reconciled against the exchanges; an audit log of "
       "everything. All of it runs against the exchanges' own test systems with play money, and the same code runs on "
       "a real-money server behind extra rails. Let's see it.",
       kind="card",
       html=card("What ApnaSOR does", "One system, <span class='thin'>every step accounted for</span>",
                 "From the order book to the audit log, one order path.",
                 bullets=["<b>Live order books</b> from Binance, Bybit, OKX, Gate and HTX — a consolidated best bid and ask",
                          "<b>Best price after fees</b>, liquidity sweep, smart routing — with a proof for every decision",
                          "<b>Seven risk checks</b> on every order, a kill switch, a loss limit, concentration alerts",
                          "<b>Execution algorithms</b>: TWAP, VWAP, implementation shortfall, POV, iceberg — or Auto",
                          "<b>Research and a copilot</b> that propose; a person confirms",
                          "<b>Wallet, P&amp;L and reconciliation</b> against each exchange; an <b>append-only audit log</b>",
                          "<b>Real money</b> behind rails: trade-only keys, hard caps, a typed go-live, a phone kill switch"],
                 foot="Sandbox = the exchanges' own test systems with play money · research is not financial advice"))
async def s03(S: lib.SceneCtx):
    await S.end(0.8)


# ── PART 2: the main parts, live ─────────────────────────────────────────────
@scene("04_login_dashboard",
       "This is the trader's screen. Log in; every login is written to the audit log. The Dashboard: four exchanges "
       "connected, a local copy of each order book kept in sync from its live feed, and on the right the best bid and "
       "the best ask across all of them for twenty coins, with the exchange that holds each price. On the left, every "
       "order the router has sent.",
       url="/login", auth=False)
async def s04(S: lib.SceneCtx):
    p = S.page
    await S.cue("Log in", 0.3)
    await S.type(p.locator("#login-user"), "admin")
    await S.type(p.locator("#login-pass"), "admin")
    await S.click(p.get_by_role("button", name=re.compile("Continue")))
    await S.cue("The Dashboard")
    await S.caption("<b>Live order books</b> from four exchanges, kept in sync from their feeds")
    await S.cue("on the right")
    await S.glide(1250, 520)
    await S.spot(card_of(p.get_by_text("Best prices", exact=True)), 4)
    await S.caption("<b>NBBO</b> — the best bid and ask across every exchange, 20 coins")
    await S.cue("On the left")
    await S.glide(580, 520)
    await S.spot(card_of(p.get_by_text("Live order flow", exact=True)), 3)
    await S.caption("<b>Every order</b> the router has sent — click one for its proof")
    await S.end()


@scene("05_order",
       "A real order, in the sandbox: buy a small amount of Bitcoin, routed by best price after fees. Submit. Seven "
       "risk checks, a funding check, then the router walks every book cheapest level first and sends a price-protected "
       "immediate-or-cancel child order. Filled. And here is the proof: every exchange ranked by all-in cost, price plus "
       "fee; the levels walked; the cap no child may cross; expected versus actual; and what it saved against the worst "
       "exchange. Every order keeps this proof.",
       url="/")
async def s05(S: lib.SceneCtx):
    p = S.page
    await S.cue("buy a small amount", 0.6)
    await S.click(p.get_by_role("button", name="New order"))
    await S.type(p.locator("input[placeholder^='e.g.']").first, "0.0006")
    await S.caption("BUY 0.0006 BTC · market · <b>best price after fees</b>")
    await S.cue("Submit")
    await S.click(p.get_by_role("button", name=re.compile("Submit buy order")))
    await S.caption("<b>7 risk checks</b> → funding gate → routing → one IOC child")
    await wait_text(p, "SOR decision", 90)
    await S.cue("Filled")
    await S.caption("<b>Filled</b> — exchange, price, fee, child order id")
    await S.cue("And here is the proof")
    await S.glide(800, 600)
    await S.scroll(380, steps=8, pause=0.1)
    await S.caption("<b>Routing proof</b> — all-in cost per exchange, levels walked, price cap, saved vs worst")
    await S.cue("the cap no child")
    await S.scroll(320, steps=8, pause=0.1)
    await S.cue("Every order keeps")
    await S.scroll(300, steps=8, pause=0.1)
    await S.end()
    await p.keyboard.press("Escape")


@scene("06_refused",
       "An order that breaks a rule: a fifth of a Bitcoin, about sixteen thousand dollars, above this account's "
       "per-order limit. Submit. Refused by pre-trade risk, with the check that failed, and nothing reached an exchange.",
       url="/")
async def s06(S: lib.SceneCtx):
    p = S.page
    await S.cue("a fifth of a Bitcoin", 1.0)
    await S.click(p.get_by_role("button", name="New order"))
    await S.type(p.locator("input[placeholder^='e.g.']").first, "0.2")
    await S.caption("BUY 0.2 BTC ≈ $16,000 · above the $10,000 per-order limit")
    await S.cue("Submit")
    await S.click(p.get_by_role("button", name=re.compile("Submit buy order")))
    try:
        await wait_text(p, "(?i)reject|refus|exceed", 30)
    except Exception:
        pass
    await S.cue("Refused by pre-trade risk")
    await S.caption("<b>Refused</b> before routing — nothing reached an exchange")
    await S.end()
    await p.keyboard.press("Escape")


@scene("07_sweep",
       "A large order should not hit one exchange. The liquidity sweep sends to every exchange that can fund it, at "
       "the same moment, each part sized by that exchange's depth, all under one price cap. A hundredth of a Bitcoin. "
       "Submit. The checks run, the children leave together, each capped at the same price. One parent order, two "
       "exchanges, and the proof shows the split and each exchange's fill.",
       url="/")
async def s07(S: lib.SceneCtx):
    p = S.page
    await S.cue("The liquidity sweep", 0.4)
    await S.click(p.get_by_role("button", name="New order"))
    sel = p.locator("select").filter(has=p.locator("option[value='liquidity_sweep']")).first
    await S.select(sel, "liquidity_sweep")
    if await sel.input_value() != "liquidity_sweep":
        await sel.focus()
        await p.keyboard.press("ArrowDown")
    await S.caption("<b>Liquidity sweep</b> — every exchange at once, sized by depth, one price cap")
    await S.cue("A hundredth of a Bitcoin", 0.3)
    await S.type(p.locator("input[placeholder^='e.g.']").first, "0.01")
    await S.cue("Submit")
    await S.click(p.get_by_role("button", name=re.compile("Submit (buy|sell) order")))
    await S.cue("The checks run")
    await S.caption("<b>7 checks</b> → funding gate → children to every exchange, one cap")
    await wait_text(p, "SOR decision", 90)
    await S.cue("One parent order")
    await S.glide(800, 600)
    await S.scroll(420, steps=10, pause=0.1)
    await S.caption("<b>One parent, two children</b> — the split and each exchange's fill")
    await S.end(1.0)
    await p.keyboard.press("Escape")


@scene("08_algos",
       "Large orders can be worked over time with five execution algorithms: TWAP, VWAP, implementation shortfall, "
       "percentage of volume, and iceberg. Or Auto reads the live books and this order, picks one, and explains why; "
       "nothing is sent. Every slice is pre-flight checked against risk. Let's start a TWAP: six slices over one "
       "minute, each one through the same order path. We'll come back for the result.",
       url="/algos")
async def s08(S: lib.SceneCtx):
    p = S.page
    await S.caption("TWAP · VWAP · implementation shortfall · POV · iceberg · <b>Auto</b>")
    await S.type(p.locator("input[placeholder='e.g. 0.002']"), "0.0012")
    await S.type(p.get_by_text("Over", exact=True).locator("xpath=following::input[1]"), "1")
    await S.type(p.get_by_text("Slices", exact=True).locator("xpath=following::input[1]"), "6")
    await S.cue("Or Auto reads")
    await S.click(p.get_by_role("button", name=re.compile("Auto")))
    await S.caption("<b>Auto</b> — recommends an algorithm with its reasons · sends nothing")
    await S.cue("Every slice is pre-flight")
    await S.click(p.get_by_role("button", name=re.compile("Pre-flight check")))
    await S.caption("<b>Pre-flight</b> — every slice checked before the first is sent")
    await asyncio.sleep(3.0)
    try:
        await p.get_by_label("Close the pre-flight result").click(timeout=3000)
    except Exception:
        await p.keyboard.press("Escape")
    await S.cue("Let's start a TWAP")
    await S.click(p.get_by_role("button", name=re.compile(r"^Start TWAP")))
    await S.caption("<b>TWAP</b> started — 0.0012 BTC, 6 slices over 1 minute")
    await S.end()


@scene("09_research",
       "Research runs on the real market's candles: five years of hourly and eight years of daily history per coin. "
       "It forecasts the chance that the next hour closes higher, with a likely range and its own track record on data "
       "it had not seen — and mostly it says no proven edge, because that is the honest answer on crypto data. A "
       "backtester and a study of classic techniques pay fees on every trade. Nothing here is advice, and nothing here "
       "places an order.",
       url="/research")
async def s09(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Forecasts</b> — probabilities, a likely range, a walk-forward track record")
    await S.cue("It forecasts the chance")
    await S.glide(900, 400)
    await S.spot(card_of(p.get_by_text("Next hour", exact=True)), 4)
    await S.cue("and mostly it says")
    await S.caption("<b>No proven edge</b> — the honest result, said on the screen")
    await S.cue("A backtester")
    await S.scroll(1150, steps=14)
    await S.caption("<b>Technique study</b> — hourly BTC, 5 years, after fees: every classic rule lost; holding won")
    await S.cue("Nothing here is advice")
    await S.caption("Research, not advice · <b>nothing here places an order</b>")
    await S.end()


@scene("10_copilot",
       "The copilot takes plain English. Buy twenty dollars of SOL with smart routing. A language model only fills a "
       "form; the code validates every field, gathers the live facts and builds the proposal. The model never holds "
       "keys, never submits, never sizes risk. Here it is. It waits for a person. Confirm, and the order takes the same "
       "path as the button, risk checks included.",
       url="/")
async def s10(S: lib.SceneCtx):
    p = S.page
    aside = await open_copilot(S)
    await S.cue("Buy twenty dollars", 0.4)
    box = aside.locator("form input").first
    await S.type(box, "Buy $20 of SOL with smart routing", delay=28)
    await p.keyboard.press("Enter")
    await S.cue("A language model only")
    await S.caption("Instruction: <b>buy $20 of SOL with smart routing</b> — the model fills a form, code validates")
    await S.cue("The model never holds")
    await S.caption("Never holds keys · never submits · never sizes risk · questions get the analyst's verdict with reasons")
    confirm = aside.get_by_role("button", name=re.compile("^Confirm"))
    await p.wait_for_function(WAIT_INPUT, timeout=90000)
    if not await confirm.count():
        raise RuntimeError("no proposal: " + (await aside.inner_text())[-200:])
    await S.cue("Here it is")
    await S.caption("A <b>proposal</b> — coin, amount, strategy, a fresh price · it waits for a person")
    await S.cue("Confirm, and the order")
    await S.click(confirm)
    await S.caption("<b>Confirmed</b> → OrderEntry.submit → the same seven checks → routing")
    try:
        await p.wait_for_function(WAIT_INPUT, timeout=90000)
    except Exception:
        pass
    await S.caption("<b>Filled</b> — proposed by the model, confirmed by a person, routed by the code")
    await S.end(1.0)


@scene("11_twap_wallet",
       "The TWAP is done: six slices, each with its own fill, and the chart against the arrival price and the real "
       "market's volume-weighted price over the same minute, the two benchmarks every algorithm reports. The Wallet "
       "reads real balances from every exchange, keeps a P and L ledger from every fill, and reconciles it against what "
       "each exchange reports. A mismatch is shown, never edited away.",
       url="/algos")
async def s11(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>TWAP</b> — 6 slices over 1 minute, every slice through OrderEntry")
    try:
        await p.get_by_text(re.compile(r"^TWAP-")).first.wait_for(timeout=5000)
        await S.click(p.get_by_text(re.compile(r"^TWAP-")).first)
    except Exception:
        pass
    await S.cue("and the chart against")
    await S.glide(1100, 600)
    await S.scroll(900, steps=12)
    await S.caption("Benchmarks: <b>arrival price</b> and the <b>market VWAP</b> over the same minute")
    await S.cue("The Wallet reads")
    await p.goto(APP + "/wallet", wait_until="networkidle")
    await S.caption("<b>Wallet</b> — real balances, a P&L ledger, reconciliation per exchange")
    await S.cue("and reconciles it")
    await S.glide(1350, 220)
    await S.spot(card_of(p.get_by_text("Reconciliation", exact=True).first), 3)
    await S.cue("A mismatch is shown")
    await S.caption("Mismatches are <b>shown, never edited</b> — accepting balances is audited")
    await S.end()


@scene("12_safety",
       "Safety. The kill switch stops every new order, algorithm and bot at once. Engage it, with a reason. The order "
       "button locks, and the first of the seven checks refuses anything else. The switch survives restarts, and is "
       "replicated by a three-node RAFT cluster with signed messages: engaging works without a majority, releasing "
       "needs one. Let's kill the leader process. A new leader within seconds, nothing lost. Two of three is a "
       "majority, so we release, and trading resumes. The limits, a loss limit that engages the switch by itself, "
       "and concentration alerts live on the Risk Manager.",
       url="/killswitch")
async def s12(S: lib.SceneCtx):
    p = S.page
    await S.caption("<b>Kill switch</b> — one switch stops every new order, algo and bot")
    await S.cue("Engage it", 0.3)
    await S.click(p.get_by_role("button", name="Engage kill switch").first)
    await S.type(p.locator("input[placeholder^='Reason']"), "Launch video: safety demo", delay=25)
    await S.click(p.get_by_role("button", name=re.compile("Confirm Kill Switch")))
    try:
        await p.get_by_role("button", name=re.compile("Release kill switch")).wait_for(timeout=15000)
    except Exception:
        pass
    await S.cue("The order button locks")
    await S.caption("<b>Engaged</b> — the order button locks; the first check refuses anything else")
    await S.cue("and is replicated")
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
    except Exception as e:
        await S.caption(f"Leader kill skipped: {e}")
    try:
        await p.get_by_text(re.compile("2 of 3 nodes up")).first.wait_for(timeout=20000)
    except Exception:
        pass
    await S.cue("A new leader")
    await S.caption("<b>New leader</b> elected — the kill switch state survived")
    await S.cue("Two of three")
    await S.glide(900, 300)
    await S.scroll(-600, steps=6)
    await S.click(p.get_by_role("button", name=re.compile("Release kill switch")))
    try:
        await wait_text(p, "Trading is running normally", 20)
    except Exception:
        pass
    await S.caption("<b>Released</b> with 2 of 3 — a majority is enough; trading resumes")
    await S.cue("The limits, a loss limit")
    await p.goto(APP + "/risk", wait_until="networkidle")
    await S.caption("<b>Risk Manager</b> — limits, a loss limit that engages the switch, concentration alerts")
    await S.end()


@scene("13_more",
       "Also in the box: bots that trade from a fenced automation fund, with a practice lab that measures every one of "
       "their trades; an arbitrage detector that watches every exchange pair after fees and, by design, cannot trade; "
       "venue health; and the audit log, the append-only record of every step with who did it.",
       url="/audit")
async def s13(S: lib.SceneCtx):
    await S.caption("<b>Bots &amp; practice lab</b> — a fenced fund, measured costs, no promise of profit")
    await S.cue("an arbitrage detector")
    await S.caption("<b>Arbitrage detector</b> — every pair, after both fees · detection only")
    await S.cue("venue health")
    await S.caption("<b>Venues</b> — latency, sync, drift from the real market")
    await S.cue("and the audit log")
    await S.scroll(450, steps=10)
    await S.caption("<b>Audit log</b> — append-only, with an actor on every line")
    await S.end()


@scene("14_real_money",
       "Everything so far ran in the exchanges' sandboxes, with play money. The same code runs on a separate real-money "
       "server with a fixed IP, reached only through an SSH tunnel. An exchange gets orders only after it confirms "
       "that our key can trade but cannot withdraw. Hard caps live in the server's settings where no screen can loosen "
       "them, going live is typed, alerts reach a private Discord channel, and a stop from the phone engages the kill "
       "switch; nothing releases it from the phone.",
       url="http://localhost:5190/login", auth=False)
async def s14(S: lib.SceneCtx):
    await S.cue("The same code runs")
    await S.caption("<b>REAL MONEY SERVER</b> — its own machine, fixed IP, reached through an SSH tunnel")
    await S.cue("An exchange gets orders")
    await S.caption("Trade-only keys, confirmed by each exchange: trade spot <b>yes</b>, withdraw <b>no</b>, IP-locked")
    await S.cue("Hard caps live")
    await S.caption("Caps in .env: <b>$25</b> per order · <b>$100</b> per algorithm · <b>$150</b> exposure · <b>$10</b> loss → kill switch")
    await S.cue("alerts reach")
    await S.caption("Discord alerts · <b>/stop</b> engages the kill switch — nothing releases it from the phone")
    await S.end()


@scene("15_close",
       "The contribution is the integration: real exchanges, fee-aware routing, deterministic risk, algorithms, "
       "research, a copilot a person confirms, and an audit trail, one system, verified live. ApnaSOR, group CS14, "
       "Usman Institute of Technology, supervised by Sir Shamaail Jafri.",
       kind="card",
       html=card("Thank you", "ApnaSOR",
                 "One order, every exchange, the best price — with risk controls a person can trust.",
                 bullets=["Sarmad Sufyan Ahmed · Fizza Mubeen · Mahnoor Nadeem · Maham Ikram",
                          "Supervisor: Sir Shamaail Jafri",
                          "Usman Institute of Technology · Group CS14 · Final Year Project"],
                 foot="Sandbox = the exchanges' own test systems with play money · research is not financial advice"))
async def s15(S: lib.SceneCtx):
    await S.end(1.5)
