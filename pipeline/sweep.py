#!/usr/bin/env python3
"""Sweep every model parameter across all backtested seasons and report deltas.

A coefficient set by judgement is a hypothesis, not a fact. This runs each
parameter over a grid, scores the three-season mean for every value, and prints
the change against the current default, so a parameter that is merely
plausible can be told apart from one that is earning its value.

Deltas are positive-is-better except rmse. Treat anything under about 0.3 of
best-XI or 0.002 of rank correlation as noise: several changes of that size
have reversed on a different season.

  python3 pipeline/sweep.py
"""
import re
import subprocess
import sys

GRID = {
 "prior_minutes": [400, 600, 900, 1400, 2000],
 "league_xgc":    [1.15, 1.35, 1.55],
 "prior_games":   [2, 5, 10, 16],
 "home_con":      [0.03, 0.08, 0.14],
 "bonus_shrink":  [0.4, 0.7, 1.0, 1.3],
 "defcon_scale":  [1.5, 3.0, 5.0],
 "p60_sub":       [0.0, 0.05, 0.15],
 "unseen_start":  [0.2, 0.35, 0.5],
 "min_prior_w":   [0.25, 0.5, 1.0],
 "min_decay":     [0.45, 0.55, 0.65],
 "prev_min_minutes": [300, 450, 700],
}
COLS = ["rank", "rmse", "xi", "cap", "cap5", "top50"]
def run(args):
    out = subprocess.run([sys.executable, "pipeline/backtest.py", "--all"] + args,
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        if line.startswith("MEAN"):
            return [float(x) for x in re.findall(r"-?\d+\.\d+", line)]
    return None
base = run([])
print("baseline" + "".join(f"{c:>9}" for c in COLS))
print(f"{'':22}" + "".join(f"{v:9.3f}" for v in base))
print("\ndeltas vs baseline (positive is better except rmse, where negative is better)\n")
print(f"{'parameter':22}" + "".join(f"{c:>9}" for c in COLS) + "   verdict")
for key, vals in GRID.items():
    for v in vals:
        r = run(["--set", f"{key}={v}"])
        if not r: continue
        d = [r[i] - base[i] for i in range(len(COLS))]
        # better if rank, xi, cap, cap5, top50 up and rmse down; weight rank/rmse/xi
        score = d[0] * 40 + (-d[1]) * 40 + d[2] * 0.5 + d[5] * 3
        flag = "  <-- better" if score > 0.25 else ("  worse" if score < -0.25 else "")
        cur = "  (current)" if abs(v - {"prior_minutes":900,"league_xgc":1.35,"prior_games":5,"home_con":0.08,"bonus_shrink":0.7,"defcon_scale":3.0,"p60_sub":0.05,"unseen_start":0.35,"min_prior_w":0.5,"min_decay":0.55,"prev_min_minutes":450}[key]) < 1e-9 else ""
        print(f"{key+'='+str(v):22}" + "".join(f"{x:+9.3f}" for x in d) + flag + cur)
    print()
