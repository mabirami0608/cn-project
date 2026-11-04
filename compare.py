#!/usr/bin/env python3
"""Aggregate results/*/summary.json: baseline vs SDN table (+ plots if matplotlib exists)."""
import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

SEV = ("CRITICAL", "ERROR", "WARNING", "INFO")
root = Path(sys.argv[1] if len(sys.argv) > 1 else "results/experiments")

runs = defaultdict(list)
for meta_path in sorted(root.glob("*/meta.json")):
    summary_path = meta_path.parent / "summary.json"
    if not summary_path.exists():
        continue
    meta, summ = json.loads(meta_path.read_text()), json.loads(summary_path.read_text())
    runs[(meta["noise_mbps"], meta["mode"])].append(summ)

if not runs:
    sys.exit(f"No finished runs in {root}/")


def avg(key_fn, items):
    vals = [v for v in (key_fn(i) for i in items) if v is not None]
    return mean(vals) if vals else None


def f(v, d=1):
    return "n/a" if v is None else f"{v:.{d}f}"


rows = []
print(f'\n{"Noise":>6} {"Mode":<9} | {"Loss% CRIT":>10} {"ERR":>6} {"WARN":>6} {"INFO":>6} {"ALL":>6} '
      f'| {"Delay ms CRIT":>13} {"P95 CRIT":>9} {"Avg ALL":>8} | {"React s":>7}')
print("-" * 106)
for (noise, mode), items in sorted(runs.items()):
    loss = {s: avg(lambda r: r["severity"][s]["loss_pct"], items) for s in SEV}
    row = dict(noise=noise, mode=mode, loss=loss,
               loss_all=avg(lambda r: r["total"]["loss_pct"], items),
               d_crit=avg(lambda r: r["severity"]["CRITICAL"]["avg_ms"], items),
               p95_crit=avg(lambda r: r["severity"]["CRITICAL"]["p95_ms"], items),
               d_all=avg(lambda r: r["total"]["avg_ms"], items),
               react=avg(lambda r: r.get("sdn_reaction_s"), items))
    rows.append(row)
    print(f'{noise:>6g} {mode:<9} | {f(loss["CRITICAL"],2):>10} {f(loss["ERROR"],2):>6} '
          f'{f(loss["WARNING"],2):>6} {f(loss["INFO"],2):>6} {f(row["loss_all"],2):>6} '
          f'| {f(row["d_crit"]):>13} {f(row["p95_crit"]):>9} {f(row["d_all"]):>8} | {f(row["react"]):>7}')

(root / "comparison.json").write_text(json.dumps(rows, indent=2))

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    print("\n(matplotlib not installed - skipping plots; pip install matplotlib)")
    sys.exit()

noises = sorted({r["noise"] for r in rows})
fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
width = 0.38
for ax, (title, getter, unit) in zip(axes, [
        ("CRITICAL log loss", lambda r: r["loss"]["CRITICAL"], "%"),
        ("CRITICAL avg delay", lambda r: r["d_crit"], "ms"),
        ("Overall loss", lambda r: r["loss_all"], "%")]):
    for i, mode in enumerate(("baseline", "sdn")):
        vals = []
        for n in noises:
            m = [r for r in rows if r["noise"] == n and r["mode"] == mode]
            vals.append((getter(m[0]) or 0) if m else 0)
        ax.bar([x + (i - 0.5) * width for x in range(len(noises))], vals, width,
               label="Baseline (FIFO)" if mode == "baseline" else "SDN priority")
    ax.set_xticks(range(len(noises)))
    ax.set_xticklabels([f"{n:g} Mbps" for n in noises])
    ax.set_xlabel("Background traffic (2 Mbps bottleneck)")
    ax.set_ylabel(unit)
    ax.set_title(title)
    ax.legend()
plt.tight_layout()
plt.savefig(root / "comparison.png", dpi=150)
print(f"\nSaved {root / 'comparison.png'} and {root / 'comparison.json'}")
