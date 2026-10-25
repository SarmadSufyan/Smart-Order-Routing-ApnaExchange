"""Review helper: one contact sheet per scene (4 frames across the clip) -> out/review/<scene>.jpg"""
import json
import subprocess
import sys

import lib

m = json.loads((lib.OUT / "manifest.json").read_text("utf-8"))
out = lib.OUT / "review"
out.mkdir(exist_ok=True)
names = sys.argv[1:] or [n for n in m if "video_dur" in m[n]]
for n in names:
    e = m[n]
    lead, vd = float(e["lead"]), float(e["video_dur"])
    span = max(0.5, vd - lead - 0.3)
    # 4 frames at 10 / 40 / 70 / 95 % of the clip, tiled 2x2 at half size
    times = [lead + span * f for f in (0.10, 0.40, 0.70, 0.95)]
    inputs = []
    for t in times:
        inputs += ["-ss", f"{t:.2f}", "-i", e["video"]]
    fc = "".join(f"[{i}:v]trim=start_frame=0:end_frame=1,scale=960:540[v{i}];" for i in range(4))
    fc += "[v0][v1]hstack[t];[v2][v3]hstack[b];[t][b]vstack[o]"
    dst = out / f"{n}.jpg"
    r = subprocess.run([lib.FFMPEG, "-y", "-loglevel", "error", *inputs, "-filter_complex", fc, "-map", "[o]",
                        "-frames:v", "1", "-q:v", "3", str(dst)], capture_output=True, text=True)
    print(n, "ok" if r.returncode == 0 else r.stderr[-300:])
