"""Assemble the launch video: each scene's clip + narration -> one MP4 with burned-in subtitles,
plus an .srt and a script with time stamps.

    python build.py [out_dir]
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import lib
import scenes
import subtitles

GAP = 0.3          # silence after each scene's narration (seconds)
FPS = 30


def run(cmd: list[str], cwd: str | None = None) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd)}\n{r.stderr[-2000:]}")


def encode_scene(name: str, e: dict, dst: Path, first: bool, last: bool) -> tuple[float, list[tuple[float, float]]]:
    """One scene -> dst (video trimmed by its lead, narration with the recorder's holds as silence).

    Returns (target length, holds). A scene whose take and narration are older than its file is kept.
    """
    lead = float(e["lead"])
    vdur = float(e["video_dur"]) - lead
    holds = [(float(a), float(h)) for a, h in e.get("holds", [])]
    adur = float(e["audio_dur"]) + sum(h for _, h in holds)
    target = max(vdur, adur + GAP)
    src_mtime = max(Path(e["video"]).stat().st_mtime, Path(e["audio"]).stat().st_mtime)
    if dst.exists() and dst.stat().st_mtime > src_mtime and abs(lib.media_duration(dst) - target) < 0.3:
        print(f"{name:>18}  (kept)                        -> {target:6.1f} s")
        return target, holds
    pad = max(0.0, target - vdur)
    vf = [f"fps={FPS}", "scale=1920:1080:flags=lanczos", "format=yuv420p"]
    if pad > 0.01:
        vf.insert(2, f"tpad=stop_mode=clone:stop_duration={pad:.3f}")
    af = [f"apad=whole_dur={target:.3f}"]
    # silence inserted where the narration waited for the app (holds), in audio-time order
    achain = "[1:a]aresample=48000"
    if holds:
        segs, pos, parts_a = [], 0.0, []
        for k, (a, h) in enumerate(sorted(holds)):
            segs.append(f"[1:a]aresample=48000,atrim=start={pos:.3f}:end={a:.3f},asetpts=PTS-STARTPTS[s{k}];"
                        f"anullsrc=r=48000:cl=mono,atrim=0:{h:.3f}[h{k}];")
            parts_a += [f"[s{k}]", f"[h{k}]"]
            pos = a
        segs.append(f"[1:a]aresample=48000,atrim=start={pos:.3f},asetpts=PTS-STARTPTS[s_end];")
        parts_a.append("[s_end]")
        achain = "".join(segs) + "".join(parts_a) + f"concat=n={len(parts_a)}:v=0:a=1,aformat=channel_layouts=mono"
    if first:
        vf.append("fade=t=in:st=0:d=0.8")
    if last:
        vf.append(f"fade=t=out:st={max(0.0, target - 1.2):.3f}:d=1.2")
        af.append(f"afade=t=out:st={max(0.0, target - 1.2):.3f}:d=1.2")
    run([lib.FFMPEG, "-y", "-loglevel", "error",
         "-ss", f"{lead:.3f}", "-i", e["video"],
         "-i", e["audio"],
         "-filter_complex", f"[0:v]{','.join(vf)}[v];{achain},{','.join(af)}[a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
         "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
         "-t", f"{target:.3f}", "-movflags", "+faststart", str(dst)])
    print(f"{name:>18}  video {vdur:6.1f}  audio {adur:6.1f}  -> {target:6.1f} s")
    return target, holds


def black_runs(path: Path) -> list[tuple[float, float]]:
    r = subprocess.run([lib.FFMPEG, "-hide_banner", "-nostats", "-i", str(path),
                        "-vf", "blackdetect=d=0.12:pix_th=0.10", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    return [(float(a), float(b)) for a, b in re.findall(r"black_start:([0-9.]+) black_end:([0-9.]+)", r.stderr)]


def fix_black(name: str, dst: Path, first: bool, last: bool, target: float) -> None:
    """Freeze the previous frame over the black flashes a page load leaves in the recording.

    Playwright's screencast goes black for a few frames while a new document mounts (a `goto` inside a
    scene). The designed fades at the start of the first scene and the end of the last are left alone.
    """
    stamp = dst.with_suffix(".blackok")
    if stamp.exists() and stamp.stat().st_mtime > dst.stat().st_mtime:
        return
    runs = [(a, b) for a, b in black_runs(dst)
            if not (first and a < 1.0) and not (last and b > target - 1.5)]
    if runs:
        # freezeframes takes two inputs (the stream and the source of the frozen frame): split before each
        graph, cur = [], "[0:v]"
        for k, (a, b) in enumerate(runs):
            first, last = max(1, int(a * FPS) - 1), int(b * FPS) + 2
            graph.append(f"{cur}split[s{k}a][s{k}b];[s{k}a][s{k}b]freezeframes=first={first}:last={last}:"
                         f"replace={max(0, first - 1)}[v{k}]")
            cur = f"[v{k}]"
        tmp = dst.with_name(dst.stem + "_fix.mp4")
        run([lib.FFMPEG, "-y", "-loglevel", "error", "-i", str(dst), "-filter_complex", ";".join(graph),
             "-map", cur, "-map", "0:a",
             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
             "-c:a", "copy", "-movflags", "+faststart", str(tmp)])
        tmp.replace(dst)
        print(f"{name:>18}  black frames frozen over: " + ", ".join(f"{a:.2f}-{b:.2f} s" for a, b in runs))
    stamp.touch()


def burn_subtitles(joined: Path, cues: list, work: Path, final: Path) -> None:
    """The .ass burned into the picture (projectors and phones show it without a player setting)."""
    subtitles.write(cues, final.with_suffix(".srt"), work / "subs.ass")
    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    for f in ("segoeui.ttf", "segoeuib.ttf"):
        src = Path(r"C:\Windows\Fonts") / f
        if src.exists() and not (fonts / f).exists():
            shutil.copy(src, fonts / f)
    run([lib.FFMPEG, "-y", "-loglevel", "error", "-i", joined.name,
         "-vf", "subtitles=subs.ass:fontsdir=fonts",
         "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "copy", "-movflags", "+faststart", str(final)], cwd=str(work))


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = json.loads((lib.OUT / "manifest.json").read_text("utf-8"))
    work = lib.OUT / "scenes"
    work.mkdir(exist_ok=True)
    names = [sc["name"] for sc in scenes.SCENES]
    parts: list[Path] = []
    rows: list[dict] = []          # where each scene sits in the final timeline (for the subtitles, the script)
    t = 0.0
    for i, name in enumerate(names):
        dst = work / f"{name}.mp4"
        target, holds = encode_scene(name, m[name], dst, first=i == 0, last=i == len(names) - 1)
        fix_black(name, dst, first=i == 0, last=i == len(names) - 1, target=target)
        parts.append(dst)
        rows.append({"name": name, "start": t, "target": target, "holds": holds})
        t += target
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), "utf-8")
    joined = work / "joined.mp4"
    run([lib.FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
         "-movflags", "+faststart", str(joined)])
    final = out_dir / "ApnaSOR-launch-video.mp4"
    cues = subtitles.build_cues(rows, m)
    burn_subtitles(joined, cues, work, final)
    total = lib.media_duration(final)
    # the script with time stamps
    lines = ["# ApnaSOR launch video — narration script", "",
             f"Length {int(total // 60)}:{int(total % 60):02d} · 1920×1080 · voice: {lib.VOICE} (edge-tts) · "
             "subtitles burned in (also ApnaSOR-launch-video.srt)", "",
             "Every claim follows README.md and the guides in project-info/ (sandbox = the exchanges' own test systems "
             "with play money; the arbitrage module detects only; forecasts are probabilities with a track record).", ""]
    for r in rows:
        start = r["start"]
        lines += [f"## {int(start // 60)}:{int(start % 60):02d}  {r['name']}", "", m[r["name"]]["narration"], ""]
    (out_dir / "ApnaSOR-launch-video-script.md").write_text("\n".join(lines), "utf-8")
    print(f"\nsubtitles: {len(cues)} cues -> {final.with_suffix('.srt').name}")
    print(f"final: {final}  ({total / 60:.1f} min)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "reports" / "launch-video")
