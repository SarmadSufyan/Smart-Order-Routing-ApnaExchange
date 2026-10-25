# The launch video, recorded on the live sandbox

`reports/launch-video/ApnaSOR-launch-video.mp4` (13.9 min, 1920×1080, voice-over) is made by these scripts
from the real web app, not from slides: Playwright drives the pages and records them, a neural voice reads
the narration, and ffmpeg joins the scenes. Every claim in the narration follows `README.md` and the guides in
`project-info/` (sandbox = the exchanges' own test systems with play money; the arbitrage module detects only;
forecasts are probabilities with a track record).

## What you need

- The laptop sandbox running: `python start_all.py` (gateway :8000, web app :5173, RAFT on).
- For the two real-money scenes: `python deploy/real-server/deploy.py tunnel` and
  `npm --prefix frontend run dev:real-server` (the screen on :5190).
- `pip install playwright edge-tts httpx` and `python -m playwright install chromium`; `ffmpeg` on the PATH.
- A Gemini key in `.env` for the two copilot scenes (the free tier sometimes answers 503: the recorder retakes
  the scene by itself, up to three times).

## How it works

| File | Does |
|---|---|
| `scenes.py` | The 25 scenes: each one's narration and what happens on the screen (clicks, typing, captions, spotlights). Also the title cards. |
| `lib.py` | Recording (1920×1080, the app zoomed 1.2× so it reads well), the cursor + caption overlay, the sandbox login, the voice (edge-tts with word timings), the scene clock. |
| `record.py` | `tts` makes the narration and checks every cue phrase; `prep` accepts the exchange balances so the Wallet starts clean; `record [scene …]` records. |
| `build.py` | One MP4: each scene's clip + narration, a title fade, the script with time stamps. |
| `sample.py` | A four-frame contact sheet per scene in `out/review/`, to check a take without watching it. |
| `check_locators.py` | A read-only check that every control the scenes click still exists. |

The scene clock follows the narration: an action is placed at the moment a phrase is spoken (`S.cue("Submit")`).
When the app or the model is slower than the words (an order filling, the copilot thinking, a RAFT node
restarting), the narration waits: the recorder notes the hold and `build.py` inserts that much silence at the
cue, so the words never run ahead of the screen.

## Make it

```powershell
python scripts/launch_video/record.py tts          # narration for every scene -> out/audio (cues checked)
python scripts/launch_video/record.py prep         # once: the wallet's opening balances (audited)
python scripts/launch_video/record.py record       # every scene; or: record 06_order 19_kill
python scripts/launch_video/sample.py              # contact sheets to review
python scripts/launch_video/build.py               # -> reports/launch-video/ApnaSOR-launch-video.mp4 + the script
```

A changed narration needs `tts <scene>` again before `record <scene>`; `build.py` re-encodes only the scenes
whose take or narration changed. Recording places real sandbox orders (about $50 of BTC, a $800 sweep, a
0.0012 BTC TWAP, two $20 SOL buys), engages and releases the kill switch, and kills the RAFT leader once.
