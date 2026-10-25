"""Subtitles for the launch video, from the voice's own word timings.

edge-tts reports when each word starts; the narration text carries the punctuation. The two are aligned
word by word, the words grouped into cues that break at sentence ends (or at a comma when a cue would get
too long), each cue shifted by the silences the recorder inserted (holds) and by where its scene sits in
the final timeline. Output: an .srt beside the video and a styled .ass that ffmpeg burns in.
"""
from __future__ import annotations

import re
from pathlib import Path

MAX_CUE = 84          # characters per cue (two lines of ~42)
LINE = 42             # wrap a cue into two lines above this
MIN_DUR = 1.0         # seconds a cue stays at least
LEAD_IN = 0.10        # show a cue slightly before its first word
SENTENCE_END = re.compile(r"[.!?;]$|[.!?;][\"')]$")
SOFT_BREAK = re.compile(r"[,:]$|—$")


def _norm(w: str) -> str:
    return re.sub(r"[^a-z0-9%$]", "", w.lower())


def align(narration: str, words: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """[(start, token with punctuation)] for every whitespace token of the narration.

    The voice may split a hyphenated word or a number into several timed words, or (rarely) merge two
    tokens; both are handled by comparing normalized text. A token the voice has no word for (a lone dash)
    takes the time of the token before it.
    """
    out: list[tuple[float, str]] = []
    i = 0
    seq = [(t, _norm(w)) for t, w in words]
    last_t = 0.0
    for tok in narration.split():
        n = _norm(tok)
        if not n:
            out.append((last_t, tok))
            continue
        if i < len(seq) and seq[i][1] == n:
            last_t = seq[i][0]
            i += 1
        else:
            # the voice split this token: consume words while their concatenation is a prefix of the token
            j, acc = i, ""
            while j < len(seq) and len(acc) < len(n) and n.startswith(acc + seq[j][1]):
                acc += seq[j][1]
                j += 1
            if acc == n and j > i:
                last_t = seq[i][0]
                i = j
            elif i < len(seq) and seq[i][1].startswith(n):
                # the voice merged this token with the next one(s): share its time, consume on the last part
                last_t = seq[i][0]
                rest = seq[i][1][len(n):]
                if not rest:
                    i += 1
                else:
                    seq[i] = (seq[i][0], rest)
            else:
                # out of step: keep the clock monotone and move on without consuming
                pass
        out.append((last_t, tok))
    return out


def group(tokens: list[tuple[float, str]]) -> list[tuple[float, float, str]]:
    """Cues as (start, end_of_last_word_start, text), broken at sentence ends and at length."""
    cues: list[tuple[float, float, str]] = []
    cur: list[tuple[float, str]] = []

    def flush(upto: int | None = None) -> None:
        nonlocal cur
        part = cur if upto is None else cur[:upto]
        if part:
            cues.append((part[0][0], part[-1][0], " ".join(t for _, t in part)))
        cur = [] if upto is None else cur[upto:]

    for t, tok in tokens:
        if cur and sum(len(x) + 1 for _, x in cur) + len(tok) > MAX_CUE:
            # too long: prefer the last soft break (comma, colon, dash) past the first third
            k = None
            for idx in range(len(cur) - 1, 0, -1):
                if SOFT_BREAK.search(cur[idx][1]) and sum(len(x) + 1 for _, x in cur[: idx + 1]) >= MAX_CUE // 3:
                    k = idx + 1
                    break
            flush(k)
        cur.append((t, tok))
        if SENTENCE_END.search(tok):
            flush()
    flush()
    return cues


def wrap(text: str) -> list[str]:
    if len(text) <= LINE:
        return [text]
    words = text.split()
    best, best_diff = 1, 10**9
    for k in range(1, len(words)):
        a, b = " ".join(words[:k]), " ".join(words[k:])
        d = abs(len(a) - len(b))
        if len(a) <= LINE + 6 and len(b) <= LINE + 6 and d < best_diff:
            best, best_diff = k, d
    return [" ".join(words[:best]), " ".join(words[best:])]


def shifted(t: float, holds: list[tuple[float, float]]) -> float:
    return t + sum(h for a, h in holds if a <= t + 1e-6)


def build_cues(timeline: list[dict], manifest: dict) -> list[tuple[float, float, str]]:
    """Cues over the whole video: (start, end, text). `timeline`: per scene name/start/target/holds."""
    out: list[tuple[float, float, str]] = []
    for sc in timeline:
        e = manifest[sc["name"]]
        words = [(float(t), w) for t, w in e["words"]]
        holds = [(float(a), float(h)) for a, h in sc.get("holds", [])]
        toks = align(e["narration"], words)
        cues = group(toks)
        audio_end = float(e["audio_dur"]) + sum(h for _, h in holds)
        for n, (s, last, text) in enumerate(cues):
            start = sc["start"] + shifted(s, holds) - LEAD_IN
            if n + 1 < len(cues):
                end = sc["start"] + shifted(cues[n + 1][0], holds) - LEAD_IN - 0.04
            else:
                end = sc["start"] + min(audio_end + 0.25, sc["target"]) - 0.02
            end = max(end, start + MIN_DUR)
            if n + 1 < len(cues):
                end = min(end, sc["start"] + shifted(cues[n + 1][0], holds) - LEAD_IN - 0.04)
            out.append((max(0.0, start), end, text))
    return out


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Sub,Segoe UI,46,&H00FFFFFF,&H00FFFFFF,&H00141418,&HA0141418,0,0,0,0,100,100,0.2,0,3,12,0,2,220,220,118,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def write(cues: list[tuple[float, float, str]], srt_path: Path, ass_path: Path) -> None:
    srt, ass = [], [ASS_HEADER]
    for n, (s, e, text) in enumerate(cues, 1):
        lines = wrap(text)
        srt.append(f"{n}\n{_ts_srt(s)} --> {_ts_srt(e)}\n" + "\n".join(lines) + "\n")
        ass.append(f"Dialogue: 0,{_ts_ass(s)},{_ts_ass(e)},Sub,,0,0,0,," + "\\N".join(lines) + "\n")
    srt_path.write_text("\n".join(srt), "utf-8")
    ass_path.write_text("".join(ass), "utf-8-sig")
