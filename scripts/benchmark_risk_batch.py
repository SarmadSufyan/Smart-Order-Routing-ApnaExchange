"""Benchmark the risk batch: one process vs a pool of worker processes.

Measures the SAME runner the gateway uses (``RiskBatchRunner``) on two
workloads, and reports only what it measured:

  checks  the seven pre-trade checks per order (microseconds each)
  stress  the checks + a Monte Carlo stress test per order (5,000 paths x 60
          one-minute steps, the gateway default) — real CPU work

For each batch size: median wall time in-process and on warm pools of
2, 4, ... processes; pool start-up (cold) is measured separately. Every pool
answer is compared with the in-process answer (must be identical).

Run from the project root:
    .venv/Scripts/python.exe scripts/benchmark_risk_batch.py           # ~1-2 min
    .venv/Scripts/python.exe scripts/benchmark_risk_batch.py --quick   # ~20 s

Writes project-info/benchmarks/risk-batch.json and risk-batch.svg.
"""

import argparse
import asyncio
import json
import os
import platform
import statistics
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.services.risk_engine.batch import (  # noqa: E402
    RiskBatchRunner,
    StressParams,
    _warm_up,
    plan_jobs,
)
from backend.services.risk_engine.checks import OrderSpec  # noqa: E402
from backend.shared.models.risk import RiskLimits  # noqa: E402

OUT_DIR = ROOT / "project-info" / "benchmarks"
SYMBOLS = {"BTC/USDT": 85_000.0, "ETH/USDT": 2_700.0, "SOL/USDT": 150.0, "BNB/USDT": 600.0, "XRP/USDT": 0.6}
# Per-minute volatility only sets the size of the simulated moves; it doesn't
# change the amount of work, so a fixed, typical value is fine here.
SIGMA = 0.0008


def basket(n: int) -> list[OrderSpec]:
    names = list(SYMBOLS)
    return [OrderSpec(names[i % len(names)], "BUY" if i % 2 == 0 else "SELL", 0.001 * (1 + i % 7)) for i in range(n)]


def jobs_for(n: int, stress: bool, paths: int):
    params = {s: StressParams(SIGMA, 60, paths) for s in SYMBOLS} if stress else None
    return plan_jobs(basket(n), SYMBOLS, limits=RiskLimits(), kill_switch_active=False,
                     positions={}, notionals={}, stress=params)


async def timed_round_robin(executors: dict, jobs, repeats: int) -> dict:
    """Median ms per executor. Executors take turns on every repeat, so a laptop
    that throttles as it heats up slows all of them alike, not just the last."""
    times: dict = {k: [] for k in executors}
    reports: dict = {}
    for _ in range(repeats):
        for key, (runner, mode) in executors.items():
            start = time.perf_counter()
            reports[key] = await runner.run(jobs, mode=mode)
            times[key].append((time.perf_counter() - start) * 1000)
    return {k: (statistics.median(v), reports[k]) for k, v in times.items()}


def same(a, b) -> bool:
    return all(x.outcome.result == y.outcome.result and x.stress == y.stress
               for x, y in zip(a.items, b.items, strict=True))


def cold_start_ms(workers: int) -> float:
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for future in [pool.submit(_warm_up) for _ in range(workers)]:
            future.result()
        elapsed = (time.perf_counter() - start) * 1000
    return elapsed


async def run(args) -> dict:
    cpus = os.cpu_count() or 2
    pools = sorted({w for w in (2, 4, cpus) if w <= cpus})
    workloads = {
        "checks": [1, 10, 100, 1_000, 10_000] if not args.quick else [1, 100, 1_000],
        "stress": [1, 2, 4, 8, 16, 32] if not args.quick else [1, 4, 16],
    }
    results: dict = {"pools": pools, "workloads": {}, "cold_start_ms": {}}

    for w in pools:
        results["cold_start_ms"][w] = round(cold_start_ms(w), 1)
        print(f"pool of {w}: cold start {results['cold_start_ms'][w]:.0f} ms")

    runners = {w: RiskBatchRunner(workers=w, pool_min_orders=1) for w in pools}
    for r in runners.values():
        r.start()
        await r._warming  # time warm pools, as the gateway keeps them
    serial_runner = RiskBatchRunner(workers=1)

    try:
        for name, sizes in workloads.items():
            rows = []
            for n in sizes:
                jobs = jobs_for(n, name == "stress", args.paths)
                repeats = args.repeats if name == "stress" else max(args.repeats, 5)
                executors = {"in-process": (serial_runner, "serial"), **{w: (r, "pool") for w, r in runners.items()}}
                measured = await timed_round_robin(executors, jobs, repeats)
                base_ms, base = measured.pop("in-process")
                row = {"orders": n, "in_process_ms": round(base_ms, 3), "pool_ms": {}, "identical": True}
                for w, (ms, rep) in measured.items():
                    row["pool_ms"][w] = round(ms, 3)
                    row["identical"] &= same(base, rep)
                rows.append(row)
                best = min(row["pool_ms"], key=row["pool_ms"].get)
                print(f"{name:>6} {n:>6} orders: in-process {base_ms:9.2f} ms | "
                      + " | ".join(f"pool {w}: {ms:9.2f} ms" for w, ms in row["pool_ms"].items())
                      + f" | best x{base_ms / row['pool_ms'][best]:.2f} ({best}p)"
                      + ("" if row["identical"] else "  !! ANSWERS DIFFER"))
            results["workloads"][name] = rows
    finally:
        for r in runners.values():
            await r.stop()

    results["machine"] = {
        "cpu_count": cpus, "physical_cores": _physical_cores(), "processor": platform.processor(), "platform": platform.platform(),
        "python": platform.python_version(),
    }
    results["stress_paths"] = args.paths
    results["repeats"] = args.repeats
    results["measured_at"] = datetime.now(UTC).isoformat(timespec="seconds")
    return results


def _physical_cores() -> int | None:
    """Logical CPUs count hyper-threads; CPU-bound speed-up tracks physical cores."""
    if sys.platform == "win32":
        import subprocess
        try:
            out = subprocess.run(["powershell", "-NoProfile", "-Command",
                                  "(Get-CimInstance Win32_Processor | Measure-Object NumberOfCores -Sum).Sum"],
                                 capture_output=True, text=True, timeout=20).stdout.strip()
            return int(out) if out.isdigit() else None
        except (OSError, subprocess.SubprocessError):
            return None
    return None


# ── a dependency-free SVG chart ─────────────────────────────────────────
COLORS = {"in-process": "#334155", 2: "#0ea5e9", 4: "#16a34a", 8: "#d97706", 16: "#9333ea"}


def svg_chart(results: dict) -> str:
    import math

    width, height, pad = 980, 430, 60
    panel_w = (width - 3 * pad) / 2
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'font-family="Segoe UI, Arial, sans-serif" font-size="12">',
             f'<rect width="{width}" height="{height}" fill="#ffffff"/>']
    titles = {"checks": "Seven pre-trade checks only (µs work per order)",
              "stress": f"Checks + Monte Carlo stress test ({results['stress_paths']:,} paths x 60 min per order)"}
    for p, (name, rows) in enumerate(results["workloads"].items()):
        x0, y0, h = pad + p * (panel_w + pad), 50, height - 120
        series = {"in-process": [r["in_process_ms"] for r in rows]}
        for w in results["pools"]:
            series[w] = [r["pool_ms"][w] if w in r["pool_ms"] else r["pool_ms"][str(w)] for r in rows]
        xs = [r["orders"] for r in rows]
        vals = [v for s in series.values() for v in s]
        lo, hi = math.log10(max(min(vals), 1e-3)), math.log10(max(vals))
        lo, hi = math.floor(lo), math.ceil(hi) if hi > lo else lo + 1
        xl, xh = math.log10(xs[0]), math.log10(xs[-1]) if xs[-1] > xs[0] else math.log10(xs[0]) + 1

        def px(v):
            return x0 + (math.log10(v) - xl) / (xh - xl) * panel_w

        def py(v):
            return y0 + h - (math.log10(max(v, 1e-3)) - lo) / (hi - lo) * h

        parts.append(f'<text x="{x0}" y="{y0 - 18}" font-size="13" font-weight="600" fill="#0f172a">'
                     f'{titles.get(name, name)}</text>')
        parts.append(f'<rect x="{x0}" y="{y0}" width="{panel_w}" height="{h}" fill="none" stroke="#cbd5e1"/>')
        for e in range(lo, hi + 1):
            y = py(10 ** e)
            label = f"{10 ** e:g} ms" if e >= 0 else f"{10 ** e * 1000:g} µs"
            parts.append(f'<line x1="{x0}" x2="{x0 + panel_w}" y1="{y}" y2="{y}" stroke="#eef2f7"/>')
            parts.append(f'<text x="{x0 - 6}" y="{y + 4}" text-anchor="end" fill="#64748b">{label}</text>')
        for n in xs:
            parts.append(f'<text x="{px(n)}" y="{y0 + h + 16}" text-anchor="middle" fill="#64748b">{n:,}</text>')
        parts.append(f'<text x="{x0 + panel_w / 2}" y="{y0 + h + 34}" text-anchor="middle" fill="#475569">'
                     'orders in the batch (log scale) — lower is faster</text>')
        # The measured result in words, at the largest batch (no claim beyond the data).
        last = rows[-1]
        best_w = min(results["pools"], key=lambda w: series[w][-1])
        ratio = last["in_process_ms"] / series[best_w][-1]
        verdict = (f"{last['orders']:,} orders: pool of {best_w} is {ratio:.1f}x faster" if ratio >= 1
                   else f"{last['orders']:,} orders: best pool is {1 / ratio:.0f}x SLOWER than one process")
        parts.append(f'<text x="{x0 + 10}" y="{y0 + 18}" font-weight="600" fill="#0f172a">{verdict}</text>')
        for key, ys in series.items():
            color = COLORS.get(key, "#94a3b8")
            pts = " ".join(f"{px(x):.1f},{py(y):.1f}" for x, y in zip(xs, ys))
            dash = ' stroke-dasharray="5 4"' if key == "in-process" else ""
            parts.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="2.2"{dash}/>')
            for x, y in zip(xs, ys):
                parts.append(f'<circle cx="{px(x):.1f}" cy="{py(y):.1f}" r="3" fill="{color}"/>')
    lx = pad
    for key in ["in-process", *results["pools"]]:
        label = "one process" if key == "in-process" else f"pool of {key} processes"
        parts.append(f'<rect x="{lx}" y="{height - 26}" width="14" height="4" fill="{COLORS.get(key, "#94a3b8")}"/>')
        parts.append(f'<text x="{lx + 20}" y="{height - 21}" fill="#334155">{label}</text>')
        lx += 190
    m = results["machine"]
    parts.append(f'<text x="{width - pad}" y="16" text-anchor="end" fill="#94a3b8">'
                 f'{m.get("physical_cores") or "?"} cores / {m["cpu_count"]} threads · Python {m["python"]} · '
                 f'median of {results.get("repeats", "?")} runs · {results["measured_at"][:10]}</text>')
    parts.append("</svg>")
    return "\n".join(parts)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--quick", action="store_true", help="fewer batch sizes and repeats")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--paths", type=int, default=5_000, help="Monte Carlo paths per order")
    parser.add_argument("--chart-only", action="store_true", help="redraw the SVG from the saved JSON")
    args = parser.parse_args()
    if args.chart_only:
        results = json.loads((OUT_DIR / "risk-batch.json").read_text(encoding="utf-8"))
        (OUT_DIR / "risk-batch.svg").write_text(svg_chart(results), encoding="utf-8")
        print(f"redrew {OUT_DIR / 'risk-batch.svg'}")
        return
    if args.quick:
        args.repeats = 1
    results = asyncio.run(run(args))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "risk-batch.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    (OUT_DIR / "risk-batch.svg").write_text(svg_chart(results), encoding="utf-8")
    print(f"\nwrote {OUT_DIR / 'risk-batch.json'} and risk-batch.svg")


if __name__ == "__main__":  # required: worker processes re-import this file on Windows
    main()
