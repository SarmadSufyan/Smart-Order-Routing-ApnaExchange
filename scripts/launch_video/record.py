"""Record the launch video's scenes: voice first (word timings), then each scene on the real app.

    python record.py tts            # narration for every scene -> out/audio, cue check
    python record.py prep           # accept exchange balances on the sandbox wallet (audited; clears old breaks)
    python record.py record [names] # record every scene (or only the named ones), manifest in out/manifest.json
"""
from __future__ import annotations

import asyncio
import json
import sys

import httpx
import lib
import scenes
from playwright.async_api import async_playwright

MANIFEST = lib.OUT / "manifest.json"


def load_manifest() -> dict:
    return json.loads(MANIFEST.read_text("utf-8")) if MANIFEST.exists() else {}


def save_manifest(m: dict) -> None:
    MANIFEST.write_text(json.dumps(m, indent=2), "utf-8")


async def do_tts(only: list[str]) -> None:
    lib.AUDIO.mkdir(parents=True, exist_ok=True)
    m = load_manifest()
    sem = asyncio.Semaphore(4)

    async def one(sc):
        path = lib.AUDIO / f"{sc['name']}.mp3"
        async with sem:
            for attempt in range(3):
                try:
                    dur, words = await lib.synth(sc["narration"], path)
                    break
                except Exception as e:  # the service sometimes drops a stream: retry
                    if attempt == 2:
                        raise
                    print(f"{sc['name']}: retry after {e}")
                    await asyncio.sleep(2)
        m.setdefault(sc["name"], {}).update({"audio": str(path), "audio_dur": dur, "words": words,
                                             "narration": sc["narration"]})
        print(f"{sc['name']:>18}  {dur:6.1f} s  {len(words)} words", flush=True)

    await asyncio.gather(*(one(sc) for sc in scenes.SCENES if not only or sc["name"] in only))
    save_manifest(m)
    total = sum(v["audio_dur"] for v in m.values() if "audio_dur" in v)
    print(f"narration total: {total / 60:.1f} min")


def check_cues() -> None:
    """Every S.cue(...) phrase in a scene must occur in its narration (checked from the source text)."""
    import inspect
    import re
    m = load_manifest()
    bad = 0
    for sc in scenes.SCENES:
        src = inspect.getsource(sc["run"])
        phrases = re.findall(r"S\.cue\(\s*\"([^\"]+)\"", src)
        words = [tuple(w) for w in m.get(sc["name"], {}).get("words", [])]
        for ph in phrases:
            try:
                lib.cue_time(words, ph)
            except KeyError as e:
                bad += 1
                print("CUE MISSING", sc["name"], e)
    print("cues ok" if not bad else f"{bad} cue problems")


def prep() -> None:
    token, _ = lib.login_token()
    h = {"Authorization": f"Bearer {token}"}
    for v in ("V1", "V4"):
        r = httpx.post(f"{lib.API}/api/wallet/baseline", json={"venue_id": v}, headers=h, timeout=60)
        print(v, r.status_code, (r.json().get("reconciliation") if r.status_code == 200 else r.text)[:1] if r.status_code == 200 else r.text[:200])
    r = httpx.get(f"{lib.API}/api/wallet/pnl", headers=h, timeout=60).json()
    for rec in r.get("reconciliation", []):
        print(rec["venue_id"], rec["status"])


async def do_record(only: list[str]) -> None:
    lib.CLIPS.mkdir(parents=True, exist_ok=True)
    m = load_manifest()
    token, user = lib.login_token()
    scenes.TOKEN = token
    async with async_playwright() as pw:
        for sc in scenes.SCENES:
            if only and sc["name"] not in only:
                continue
            entry = m.get(sc["name"])
            if not entry or "words" not in entry:
                raise SystemExit(f"no narration for {sc['name']}: run `record.py tts` first")
            words = [tuple(w) for w in entry["words"]]
            print(f"--- {sc['name']} ({entry['audio_dur']:.1f} s)", flush=True)
            url = sc["url"] if sc["url"].startswith("http") else lib.APP + sc["url"]
            for take in range(1, 6):   # a scene that depends on an outside service may need another take
                try:
                    if sc["kind"] == "card":
                        res = await lib.record_scene(pw, sc["name"], None, sc["run"], entry["audio_dur"], words,
                                                     token, user, html=sc["html"])
                    else:
                        tk, us = (token, user) if sc["auth"] else ("", {})
                        res = await lib.record_scene(pw, sc["name"], url, sc["run"], entry["audio_dur"], words, tk, us)
                    break
                except Exception as e:
                    print(f"    take {take} failed: {str(e)[:300]}", flush=True)
                    if take == 5:
                        raise
                    await asyncio.sleep(45)   # the model's free tier recovers on its own after a while
            vdur = lib.media_duration(lib.CLIPS / f"{sc['name']}.webm")
            entry.update({"video": res["video"], "lead": res["lead"], "video_dur": vdur, "holds": res.get("holds", [])})
            if res.get("holds"):
                print("    holds:", ", ".join(f"{h:.1f}s at {a:.1f}s" for a, h in res["holds"]), flush=True)
            m[sc["name"]] = entry
            save_manifest(m)
            print(f"    clip {vdur:.1f} s (lead {res['lead']:.2f} s)", flush=True)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "tts"
    names = sys.argv[2:]
    if cmd == "tts":
        asyncio.run(do_tts(names))
        check_cues()
    elif cmd == "cues":
        check_cues()
    elif cmd == "prep":
        prep()
    elif cmd == "record":
        asyncio.run(do_record(names))
    else:
        raise SystemExit(__doc__)
