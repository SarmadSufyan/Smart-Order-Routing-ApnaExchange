"""Shared pieces of the launch-video pipeline: browser recording, cursor + caption overlay,
the sandbox login, word-timed narration (edge-tts) and ffmpeg helpers."""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
import time
from pathlib import Path

import edge_tts
import httpx
from playwright.async_api import Page

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "out"                        # recordings, narration, sheets (gitignored)
CLIPS = OUT / "clips"
AUDIO = OUT / "audio"
APP = "http://localhost:5173"
API = "http://127.0.0.1:8000"
VIEW = {"width": 1920, "height": 1080}     # the viewport = the recorded frame
REC = {"width": 1920, "height": 1080}
ZOOM = 1.2                                 # CSS zoom on the app pages: laid out as 1600x900, drawn at 1920x1080
VOICE = "en-US-AndrewNeural"
RATE = "-3%"
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"

ZOOM_JS = """
(() => { const apply = () => { if (document.documentElement && ['5173', '5190'].includes(location.port))
  document.documentElement.style.zoom = '__ZOOM__'; }; apply(); document.addEventListener('DOMContentLoaded', apply); })();
""".replace("__ZOOM__", str(ZOOM))

# ── overlay: a visible cursor, a click ripple and a caption bar ──────────────
OVERLAY_JS = r"""
(() => {
  if (window.__apnasorOverlay) return;
  window.__apnasorOverlay = true;
  const style = document.createElement('style');
  style.textContent = `
    #__cur { position: fixed; z-index: 2147483647; width: 22px; height: 22px; pointer-events: none;
             transform: translate(-3px, -3px); filter: drop-shadow(0 1px 2px rgba(0,0,0,.6)); }
    #__rip { position: fixed; z-index: 2147483646; width: 44px; height: 44px; border-radius: 50%;
             border: 2px solid rgba(255,255,255,.9); pointer-events: none; opacity: 0;
             transform: translate(-50%, -50%) scale(.3); }
    #__rip.go { animation: __ripple .45s ease-out; }
    @keyframes __ripple { 0% { opacity: .9; transform: translate(-50%,-50%) scale(.3);} 100% { opacity: 0; transform: translate(-50%,-50%) scale(1.3);} }
    #__cap { position: fixed; z-index: 2147483645; left: 36px; bottom: 34px; max-width: 62%;
             padding: 12px 18px; border-radius: 12px; background: rgba(12,12,14,.82); color: #fff;
             font: 600 19px/1.35 Inter, "Segoe UI", system-ui, sans-serif; letter-spacing: .1px;
             border: 1px solid rgba(255,255,255,.12); box-shadow: 0 8px 30px rgba(0,0,0,.45);
             backdrop-filter: blur(10px); opacity: 0; transform: translateY(8px);
             transition: opacity .35s ease, transform .35s ease; pointer-events: none; }
    #__cap.on { opacity: 1; transform: translateY(0); }
    #__cap b { color: #7ee787; font-weight: 700; }
    .__spot { outline: 2px solid rgba(255,255,255,.95) !important; outline-offset: 3px !important;
              box-shadow: 0 0 0 6px rgba(255,255,255,.14), 0 0 28px rgba(255,255,255,.25) !important;
              border-radius: 8px; transition: box-shadow .25s ease; }
  `;
  const cur = document.createElement('div'); cur.id = '__cur';
  cur.innerHTML = `<svg viewBox="0 0 24 24" width="22" height="22"><path d="M5 3l14 8.5-6.3 1.3L9.3 19z" fill="#fff" stroke="#000" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
  const rip = document.createElement('div'); rip.id = '__rip';
  const cap = document.createElement('div'); cap.id = '__cap';
  let x = -100, y = -100;
  cur.style.left = x + 'px'; cur.style.top = y + 'px';
  // The root element may not exist yet when an init script runs: mount lazily, on first use and on load.
  const mount = () => {
    const root = document.documentElement; if (!root) return false;
    for (const el of [style, cur, rip, cap]) if (!el.isConnected) root.appendChild(el);
    return true;
  };
  document.addEventListener('DOMContentLoaded', mount);
  const z = () => parseFloat(document.documentElement && document.documentElement.style.zoom) || 1;
  document.addEventListener('mousemove', (e) => { mount(); x = e.clientX / z(); y = e.clientY / z(); cur.style.left = x + 'px'; cur.style.top = y + 'px'; }, true);
  document.addEventListener('mousedown', (e) => { mount(); rip.style.left = (e.clientX / z()) + 'px'; rip.style.top = (e.clientY / z()) + 'px';
    rip.classList.remove('go'); void rip.offsetWidth; rip.classList.add('go'); }, true);
  window.__caption = (html) => { mount(); if (!html) { cap.classList.remove('on'); return; } cap.innerHTML = html; cap.classList.add('on'); };
  window.__cursorVisible = (on) => { mount(); cur.style.display = on ? '' : 'none'; };
  mount();
})();
"""


def login_token() -> tuple[str, dict]:
    """The sandbox demo login (admin / admin) through the gateway: token + user."""
    r = httpx.post(f"{API}/api/auth/login", json={"username": "admin", "password": "admin"}, timeout=15)
    r.raise_for_status()
    d = r.json()
    return d["access_token"], d["user"]


def auth_init_script(token: str, user: dict) -> str:
    return (
        "try { if (location.port === '5173') { sessionStorage.setItem('poc.token', " + json.dumps(token) + ");"
        " sessionStorage.setItem('poc.authed', '1'); sessionStorage.setItem('poc.user', "
        + json.dumps(json.dumps(user)) + "); } } catch (e) {}"
    )


# ── narration ────────────────────────────────────────────────────────────────
def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9%$]", "", w.lower())


async def synth(text: str, path: Path) -> tuple[float, list[tuple[float, str]]]:
    """Speak `text` to `path` (mp3). Returns (duration seconds, [(start_s, word), ...])."""
    com = edge_tts.Communicate(text, VOICE, rate=RATE, boundary="WordBoundary")
    words: list[tuple[float, str]] = []
    audio = bytearray()
    async for chunk in com.stream():
        if chunk["type"] == "audio":
            audio += chunk["data"]
        elif chunk["type"] == "WordBoundary":
            words.append((chunk["offset"] / 1e7, chunk["text"]))
    await asyncio.to_thread(path.write_bytes, bytes(audio))
    return await asyncio.to_thread(media_duration, path), words


def media_duration(path: Path) -> float:
    out = subprocess.run([FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def cue_time(words: list[tuple[float, str]], phrase: str) -> float:
    """When the narrator starts saying `phrase` (first word's start)."""
    target = [_norm(w) for w in phrase.split() if _norm(w)]
    seq = [_norm(w) for _, w in words]
    n = len(target)
    for i in range(len(seq) - n + 1):
        if seq[i:i + n] == target:
            return words[i][0]
    raise KeyError(f"cue not found in narration: {phrase!r}")


# ── one scene ────────────────────────────────────────────────────────────────
class SceneCtx:
    """What a scene's `run` gets: the page, a clock aligned with the narration, cues, captions."""

    def __init__(self, page: Page, audio_dur: float, words: list[tuple[float, str]], name: str):
        self.page = page
        self.audio_dur = audio_dur
        self.words = words
        self.name = name
        self.t0 = time.monotonic()
        # When the app or the model is slower than the narration, the narration waits: arriving late at a
        # cue records a hold (silence inserted into the audio at that cue) and shifts everything after it.
        self.shift = 0.0
        self.holds: list[tuple[float, float]] = []   # (audio time, seconds of silence)

    # time
    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    async def at(self, t: float, audio_pos: float | None = None) -> None:
        target = t + self.shift
        now = self.elapsed()
        if now > target + 0.15 and audio_pos is not None:
            hold = now - target
            self.holds.append((audio_pos, hold))
            self.shift += hold
            return
        if target > now:
            await asyncio.sleep(target - now)

    async def cue(self, phrase: str, lead: float = 0.0) -> None:
        c = cue_time(self.words, phrase)
        await self.at(c - lead, audio_pos=c - lead)

    async def wait(self, s: float) -> None:
        await asyncio.sleep(s)

    async def end(self, tail: float = 0.8) -> None:
        await self.at(self.audio_dur + tail)

    # overlay
    async def caption(self, html: str | None) -> None:
        await self.page.evaluate("(h) => window.__caption && window.__caption(h)", html)

    async def cursor(self, on: bool) -> None:
        await self.page.evaluate("(o) => window.__cursorVisible && window.__cursorVisible(o)", on)

    async def spot(self, locator, seconds: float = 2.2) -> None:
        """Outline an element for a moment (the thing the narrator is talking about)."""
        try:
            await locator.first.evaluate(
                "(el, s) => { el.classList.add('__spot'); setTimeout(() => el.classList.remove('__spot'), s * 1000) }",
                seconds)
        except Exception:
            pass

    # mouse
    async def glide(self, x: float, y: float, steps: int = 8) -> None:
        """Move to a point given in the app's 1600x900 layout space (scaled by the zoom)."""
        await self.page.mouse.move(x * ZOOM, y * ZOOM, steps=steps)

    async def glide_to(self, locator, dx: float = 0, dy: float = 0) -> tuple[float, float]:
        box = await locator.first.bounding_box()
        if box is None:
            await locator.first.scroll_into_view_if_needed()
            box = await locator.first.bounding_box()
        x = box["x"] + box["width"] / 2 + dx
        y = box["y"] + box["height"] / 2 + dy
        await self.page.mouse.move(x, y, steps=8)
        return x, y

    async def click(self, locator, pause: float = 0.25) -> None:
        x, y = await self.glide_to(locator)
        await asyncio.sleep(pause)
        await self.page.mouse.click(x, y)

    async def type(self, locator, text: str, delay: int = 45) -> None:
        await self.click(locator)
        await locator.first.fill("")
        await self.page.keyboard.type(text, delay=delay)

    async def select(self, locator, value: str) -> None:
        await self.glide_to(locator)
        await locator.first.select_option(value)

    async def scroll(self, dy: int, steps: int = 8, pause: float = 0.08) -> None:
        for _ in range(steps):
            await self.page.mouse.wheel(0, dy / steps)
            await asyncio.sleep(pause)


async def new_recording_page(pw, name: str, token: str, user: dict, record: bool = True):
    """A fresh browser + context (optionally recording) with the overlay and the login injected."""
    browser = await pw.chromium.launch(args=["--force-device-scale-factor=1", "--hide-scrollbars"])
    kwargs = dict(viewport=VIEW, device_scale_factor=1, reduced_motion="no-preference")
    if record:
        kwargs.update(record_video_dir=str(CLIPS / "_raw" / name), record_video_size=REC)
    ctx = await browser.new_context(**kwargs)
    await ctx.add_init_script(auth_init_script(token, user))
    await ctx.add_init_script(ZOOM_JS)
    await ctx.add_init_script(OVERLAY_JS)
    page = await ctx.new_page()
    page.on("dialog", lambda d: asyncio.ensure_future(d.accept()))
    return browser, ctx, page


async def record_scene(pw, name: str, start_url: str | None, run, audio_dur: float, words, token: str, user: dict,
                       html: str | None = None) -> dict:
    """Record one scene into CLIPS/<name>.webm. Returns {"lead": seconds to trim at the start}."""
    raw_dir = CLIPS / "_raw" / name
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    browser, ctx, page = await new_recording_page(pw, name, token, user, record=True)
    t_created = time.monotonic()
    if html is not None:
        await page.set_content(html, wait_until="load")
        await page.evaluate("() => { const c = document.getElementById('__cur'); if (c) c.style.display = 'none'; }")
    else:
        await page.goto(start_url or APP, wait_until="networkidle")
        await page.mouse.move(VIEW["width"] * 0.55, VIEW["height"] * 0.62)
    await asyncio.sleep(0.3)
    lead = time.monotonic() - t_created
    S = SceneCtx(page, audio_dur, words, name)
    try:
        await run(S)
    finally:
        await asyncio.sleep(0.2)
        await ctx.close()
        await browser.close()
    raw = next(raw_dir.glob("*.webm"))
    dst = CLIPS / f"{name}.webm"
    if dst.exists():
        dst.unlink()
    raw.replace(dst)
    return {"lead": lead, "video": str(dst), "holds": S.holds}
