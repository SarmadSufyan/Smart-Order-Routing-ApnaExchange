"""Assemble the launch video: each scene's clip + narration -> one MP4, plus a script with time stamps.

    python build.py [out_dir]
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import lib
import scenes

GAP = 0.5          # silence after each scene's narration (seconds)
FPS = 30


def run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f"ffmpeg failed: {' '.join(cmd)}\n{r.stderr[-2000:]}")


def main(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    m = json.loads((lib.OUT / "manifest.json").read_text("utf-8"))
    work = lib.OUT / "scenes"
    work.mkdir(exist_ok=True)
    parts: list[Path] = []
    timeline: list[tuple[float, str, str]] = []
    t = 0.0
    names = [sc["name"] for sc in scenes.SCENES]
    for i, name in enumerate(names):
        e = m[name]
        lead = float(e["lead"])
        vdur = float(e["video_dur"]) - lead
        holds = [(float(a), float(h)) for a, h in e.get("holds", [])]
        adur = float(e["audio_dur"]) + sum(h for _, h in holds)
        target = max(vdur, adur + GAP)
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
        if i == 0:
            vf.append("fade=t=in:st=0:d=0.8")
        if i == len(names) - 1:
            vf.append(f"fade=t=out:st={max(0.0, target - 1.2):.3f}:d=1.2")
            af.append(f"afade=t=out:st={max(0.0, target - 1.2):.3f}:d=1.2")
        dst = work / f"{name}.mp4"
        src_mtime = max(Path(e["video"]).stat().st_mtime, Path(e["audio"]).stat().st_mtime)
        if dst.exists() and dst.stat().st_mtime > src_mtime and abs(lib.media_duration(dst) - target) < 0.3:
            parts.append(dst)
            timeline.append((t, name, e["narration"]))
            t += target
            print(f"{name:>18}  (kept)                        -> {target:6.1f} s   (at {t - target:6.1f} s)")
            continue
        run([lib.FFMPEG, "-y", "-loglevel", "error",
             "-ss", f"{lead:.3f}", "-i", e["video"],
             "-i", e["audio"],
             "-filter_complex", f"[0:v]{','.join(vf)}[v];{achain},{','.join(af)}[a]",
             "-map", "[v]", "-map", "[a]",
             "-c:v", "libx264", "-preset", "medium", "-crf", "19", "-pix_fmt", "yuv420p", "-r", str(FPS),
             "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
             "-t", f"{target:.3f}", "-movflags", "+faststart", str(dst)])
        parts.append(dst)
        timeline.append((t, name, e["narration"]))
        t += target
        print(f"{name:>18}  video {vdur:6.1f}  audio {adur:6.1f}  -> {target:6.1f} s   (at {t - target:6.1f} s)")
    lst = work / "concat.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), "utf-8")
    final = out_dir / "ApnaSOR-launch-video.mp4"
    run([lib.FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
         "-movflags", "+faststart", str(final)])
    total = lib.media_duration(final)
    # the script with time stamps
    lines = ["# ApnaSOR launch video — narration script", "",
             f"Length {int(total // 60)}:{int(total % 60):02d} · 1920×1080 · voice: {lib.VOICE} (edge-tts)", "",
             "Every claim follows README.md and the guides in project-info/ (sandbox = the exchanges' own test systems "
             "with play money; the arbitrage module detects only; forecasts are probabilities with a track record).", ""]
    for start, name, text in timeline:
        lines.append(f"## {int(start // 60)}:{int(start % 60):02d}  {name}")
        lines.append("")
        lines.append(text)
        lines.append("")
    (out_dir / "ApnaSOR-launch-video-script.md").write_text("\n".join(lines), "utf-8")
    print(f"\nfinal: {final}  ({total / 60:.1f} min)")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[2] / "reports" / "launch-video")
