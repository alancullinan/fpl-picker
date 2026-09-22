#!/usr/bin/env python3
"""Score team-news claims, and your own overrides, against what actually happened.

Two questions, both about the model's biggest measured gap - who plays:

1. Claims. news.py records what managers, reporters and FPL analysts said about
   who will start. For each claim this asks: did it come true, and did the model
   already know? A claim that agrees with the model adds nothing even when right.
   What matters is the net of the cases they disagree on - claim right and model
   wrong, minus claim wrong and model right - broken down by outlet, so each
   source earns (or loses) trust over the season.

2. Overrides. When the XI you fielded differs from the best XI the model would
   have picked from the same fifteen, this counts the points your changes won or
   lost, and does the same for the captaincy.

Reads data/history/gwNN.json (the last prediction before each deadline, with
"actual" and "picks" added by build.py once the gameweek is finished) and
data/news/gwNN.json. Standard library only, costs nothing, safe to run on every
refresh. Nothing here feeds expected points: it decides whether anything should.

  python3 pipeline/score_news.py
  python3 pipeline/score_news.py --json data/news/scores.json
"""
import argparse
import glob
import json
import os
from collections import defaultdict

# Which way each claim points, and what outcome settles it.
STARTS_NO = {"benched", "out", "rotation_risk", "doubt"}
STARTS_YES = {"expected_to_start"}
PLAYS_YES = {"returning"}
UNSCORED = {"role_change", "penalties", "set_pieces"}   # recorded, not yet checkable here
MIN_SAMPLE = 30   # below this a group's record is shown but not worth acting on


def load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def outlet_of(sig):
    """Older files have only a free-text source; take its first clause as the outlet."""
    if sig.get("outlet"):
        return sig["outlet"].strip()
    src = (sig.get("source") or "unknown").strip()
    for sep in (",", " - ", " – ", " (", ";"):
        src = src.split(sep)[0]
    return src.strip() or "unknown"


def score_claims(hist_dir, news_dir):
    """One row per scorable claim: its direction, the model's, and the outcome."""
    rows, pending, unscored = [], 0, 0
    for npath in sorted(glob.glob(os.path.join(news_dir, "gw[0-9][0-9].json"))):
        news = load(npath) or {}
        gw = news.get("gw")
        snap = load(os.path.join(hist_dir, f"gw{gw:02d}.json")) if gw else None
        for sig in news.get("signals", []):
            kind = sig.get("signal")
            if kind in UNSCORED:
                unscored += 1
                continue
            if not snap or "actual" not in snap:
                pending += 1
                continue
            pid = str(sig.get("id"))
            pred = (snap.get("players") or {}).get(pid)
            act = snap["actual"].get(pid, [0, 0, 0])   # absent from history: did not feature
            if pred is None:
                continue
            p_play = pred[1]
            # p_60 is the nearer thing to a start; files from before it was kept
            # fall back to p_play, which flatters the model on substitutes.
            p_start = pred[3] if len(pred) > 3 and pred[3] is not None else p_play
            if kind in STARTS_NO or kind in STARTS_YES:
                claim, model, outcome = kind in STARTS_YES, p_start >= 0.5, act[1] > 0
            elif kind in PLAYS_YES:
                claim, model, outcome = True, p_play >= 0.5, act[0] > 0
            else:
                continue
            rows.append({"gw": gw, "id": pid, "player": sig.get("player"), "team": sig.get("team"),
                         "signal": kind, "confidence": sig.get("confidence"),
                         "outlet": outlet_of(sig), "kind": sig.get("kind") or "unknown",
                         "claim_right": claim == outcome, "model_right": model == outcome,
                         "disagree": claim != model})
    return rows, pending, unscored


def tally(rows):
    t = {"n": len(rows), "claim_right": 0, "model_right": 0, "added": 0, "cost": 0}
    for r in rows:
        t["claim_right"] += r["claim_right"]
        t["model_right"] += r["model_right"]
        if r["disagree"]:
            t["added" if r["claim_right"] else "cost"] += 1
    t["net"] = t["added"] - t["cost"]
    return t


def best_xi(ids, xp, pos):
    """The model's XI from a fifteen: legal formation with the most expected points."""
    by = {k: sorted([i for i in ids if pos.get(i) == k], key=lambda i: -xp.get(i, 0)) for k in (1, 2, 3, 4)}
    best = None
    for d in range(3, 6):
        for m in range(2, 6):
            f = 10 - d - m
            if not 1 <= f <= 3 or not by[1] or d > len(by[2]) or m > len(by[3]) or f > len(by[4]):
                continue
            xi = [by[1][0]] + by[2][:d] + by[3][:m] + by[4][:f]
            v = sum(xp.get(i, 0) for i in xi)
            if best is None or v > best[0]:
                best = (v, xi)
    return best[1] if best else []


def score_overrides(hist_dir, bundle):
    pos = {str(p["id"]): p["pos"] for p in (bundle or {}).get("players", [])}
    name = {str(p["id"]): p["name"] for p in (bundle or {}).get("players", [])}
    out = []
    for path in sorted(glob.glob(os.path.join(hist_dir, "gw[0-9][0-9].json"))):
        snap = load(path) or {}
        if "picks" not in snap or "actual" not in snap:
            continue
        chip = snap.get("chip")
        xp = {i: v[0] for i, v in snap["players"].items()}
        pts = {i: a[2] for i, a in snap["actual"].items()}
        squad = [str(p["id"]) for p in snap["picks"]]
        yours = {str(p["id"]) for p in snap["picks"] if p["slot"] <= 11}
        cap = next((str(p["id"]) for p in snap["picks"] if p["c"]), None)
        model = set(best_xi(squad, xp, pos))
        model_cap = max(model, key=lambda i: xp.get(i, 0)) if model else None
        row = {"gw": snap["gw"], "chip": chip, "xi": 0, "captain": 0,
               "started": [name.get(i, i) for i in sorted(yours - model)],
               "benched": [name.get(i, i) for i in sorted(model - yours)]}
        if chip != "bboost":   # with every player counting, the XI choice cost nothing
            row["xi"] = sum(pts.get(i, 0) for i in yours - model) - sum(pts.get(i, 0) for i in model - yours)
        if cap and model_cap and cap != model_cap:
            mult = 2 if chip == "3xc" else 1   # the extra the armband adds on top of the XI
            row["captain"] = mult * (pts.get(cap, 0) - pts.get(model_cap, 0))
            row["cap"] = (name.get(cap, cap), name.get(model_cap, model_cap))
        out.append(row)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", default="data/history")
    ap.add_argument("--news", default="data/news")
    ap.add_argument("--data", default="data/fpl.json")
    ap.add_argument("--json", help="also write the summary here")
    a = ap.parse_args()

    rows, pending, unscored = score_claims(a.history, a.news)
    overall = tally(rows)
    groups = {}
    for key in ("outlet", "kind", "signal", "confidence"):
        g = defaultdict(list)
        for r in rows:
            g[r[key]].append(r)
        groups[key] = {k: tally(v) for k, v in sorted(g.items(), key=lambda kv: -len(kv[1]))}

    print(f"CLAIMS: {overall['n']} scored, {pending} awaiting results, {unscored} role claims not yet checkable")
    if rows:
        n = overall["n"]
        print(f"  claim right {overall['claim_right']}/{n}, model right {overall['model_right']}/{n}")
        print(f"  where they disagreed: claim right {overall['added']}, model right {overall['cost']} "
              f"-> net {overall['net']:+d} correct calls the claims would have added")
        for key in ("outlet", "kind", "signal", "confidence"):
            print(f"\n  by {key}:")
            for k, t in groups[key].items():
                flag = "" if t["n"] >= MIN_SAMPLE else "  (too few to judge)"
                print(f"    {k[:34]:34} n={t['n']:3}  right {t['claim_right']:3}  vs model {t['model_right']:3}"
                      f"  net {t['net']:+3d}{flag}")

    ov = score_overrides(a.history, load(a.data))
    total = sum(r["xi"] + r["captain"] for r in ov)
    print(f"\nYOUR OVERRIDES: {len(ov)} gameweeks with picks and results, net {total:+d} points against the model's XI")
    for r in ov:
        bits = []
        if r["started"] or r["benched"]:
            bits.append(f"started {', '.join(r['started']) or '-'} over {', '.join(r['benched']) or '-'}: {r['xi']:+d}")
        if r.get("cap"):
            bits.append(f"captained {r['cap'][0]} over {r['cap'][1]}: {r['captain']:+d}")
        chip = f" [{r['chip']}]" if r["chip"] else ""
        print(f"  GW{r['gw']}{chip}: " + ("; ".join(bits) if bits else "same XI and captain as the model"))

    if a.json:
        os.makedirs(os.path.dirname(a.json) or ".", exist_ok=True)
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"claims": overall, "pending": pending, "unscored": unscored, "groups": groups,
                       "min_sample": MIN_SAMPLE, "overrides": ov, "overrides_net": total}, f, indent=1)
        print(f"\nwrote {a.json}")


if __name__ == "__main__":
    main()
