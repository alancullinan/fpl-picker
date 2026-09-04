#!/usr/bin/env python3
"""Score the expected-points model against a finished season.

Replays the season one deadline at a time, rebuilding what the model would
have known (season totals to date, fixtures ahead, last season as prior) and
comparing its next-gameweek prediction with the points actually scored.

Data comes from the Vaastav mirror (data/raw/seasons/<season>/), fetched with
--fetch. Two baselines are scored the same way: the dataset's own xP column
(Vaastav's expected-points estimate) and simple points-per-game form.

Limits: availability (injury flags, news) is not recorded historically, so
every variant is scored blind to it. The absolute numbers are therefore lower
than live accuracy; the comparison between variants is what matters.

Usage:
  python3 pipeline/backtest.py --season 2025-26 --prior 2024-25 --fetch
  python3 pipeline/backtest.py --season 2025-26 --prior 2024-25 --out data/backtest.json
  python3 pipeline/backtest.py --set att_fdr=0.2 --set prior_minutes=600   # try other parameters
"""
import argparse
import csv
import json
import os
import sys
import urllib.request
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402

VAASTAV = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
POS_ID = {"GKP": 1, "GK": 1, "DEF": 2, "MID": 3, "FWD": 4}
SLOTS = {1: 1, 2: 4, 3: 4, 4: 2}  # a 4-4-2 "best XI" for scoring


def f(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def fetch(season, root):
    d = os.path.join(root, season)
    os.makedirs(os.path.join(d, "gws"), exist_ok=True)
    for name in ("gws/merged_gw.csv", "fixtures.csv", "teams.csv", "players_raw.csv"):
        url = f"{VAASTAV}/{season}/{name}"
        req = urllib.request.Request(url, headers={"User-Agent": "fpl-picker backtest"})
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
        with open(os.path.join(d, name), "wb") as fh:
            fh.write(body)
        print(f"fetched {season}/{name} ({len(body)//1024} KB)")


def read_csv(path):
    with open(path, encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_season(root, season):
    d = os.path.join(root, season)
    gws = read_csv(os.path.join(d, "gws", "merged_gw.csv"))
    fixtures = read_csv(os.path.join(d, "fixtures.csv"))
    teams = {int(t["id"]): t for t in read_csv(os.path.join(d, "teams.csv"))}
    raw = {int(p["id"]): p for p in read_csv(os.path.join(d, "players_raw.csv"))}
    name_to_id = {t["name"]: tid for tid, t in teams.items()}
    rows = defaultdict(list)  # gw -> rows
    for r in gws:
        r["_gw"] = int(r["GW"])
        r["_el"] = int(r["element"])
        r["_team"] = name_to_id.get(r["team"])
        rows[r["_gw"]].append(r)
    fx_by_gw = defaultdict(list)
    for x in fixtures:
        if x.get("event"):
            fx_by_gw[int(float(x["event"]))].append(x)
    return rows, fx_by_gw, teams, raw


def load_prior(root, season):
    if not season:
        return {}
    path = os.path.join(root, season, "players_raw.csv")
    if not os.path.exists(path):
        return {}
    return {int(p["code"]): p for p in read_csv(path)}


def spearman(a, b):
    n = len(a)
    if n < 3:
        return 0.0

    def ranks(v):
        order = sorted(range(n), key=lambda i: v[i])
        rk = [0.0] * n
        i = 0
        while i < n:
            j = i
            while j + 1 < n and v[order[j + 1]] == v[order[i]]:
                j += 1
            avg = (i + j) / 2.0 + 1
            for k in range(i, j + 1):
                rk[order[k]] = avg
            i = j + 1
        return rk
    ra, rb = ranks(a), ranks(b)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else 0.0


def best_xi_points(preds, actual, pos):
    """Actual points of the top 1-4-4-2 picked by prediction."""
    total = 0.0
    for p, n in SLOTS.items():
        ids = [i for i in preds if pos[i] == p]
        ids.sort(key=lambda i: preds[i], reverse=True)
        total += sum(actual[i] for i in ids[:n])
    return total


def score(preds, actual, pos):
    ids = list(preds)
    a = [preds[i] for i in ids]
    b = [actual[i] for i in ids]
    rmse = (sum((x - y) ** 2 for x, y in zip(a, b)) / len(ids)) ** 0.5
    top = sorted(ids, key=lambda i: preds[i], reverse=True)
    cap = top[0]
    top5_actual = set(sorted(ids, key=lambda i: actual[i], reverse=True)[:5])
    return {
        "spearman": spearman(a, b),
        "rmse": rmse,
        "xi": best_xi_points(preds, actual, pos),
        "captain_pts": actual[cap],
        "captain_top5": 1.0 if cap in top5_actual else 0.0,
        "top50": sum(actual[i] for i in top[:50]) / min(50, len(top)),
    }


def run(rows, fx_by_gw, teams, raw, prior_by_code, P, first_gw=2, last_gw=38):
    """Replay the season; return per-gameweek metrics for the model and baselines."""
    # Season-to-date state per player, rebuilt incrementally.
    state = {}
    team_games = defaultdict(int)
    gk_by_team = defaultdict(set)
    results = defaultdict(list)  # variant -> [metrics per gw]
    per_gw = []
    all_gws = sorted(rows)
    prices = {}
    median_price = {}

    for gw in all_gws:
        if first_gw <= gw <= last_gw and gw in rows and gw - 1 in rows:
            # Predictions for this gameweek using state from gameweeks < gw.
            fixtures_this = defaultdict(list)
            for x in fx_by_gw.get(gw, []):
                th, ta = int(x["team_h"]), int(x["team_a"])
                fixtures_this[th].append({"fdr": int(f(x.get("team_h_difficulty"), 3)), "home": True})
                fixtures_this[ta].append({"fdr": int(f(x.get("team_a_difficulty"), 3)), "home": False})
            lam = {}
            for tid in teams:
                gks = [state[e] for e in gk_by_team[tid] if e in state and state[e]["mins"] > 0]
                lam[tid] = model.team_xgc(gks, P)
            if not median_price:
                by_pos = defaultdict(list)
                for r in rows[gw]:
                    by_pos[POS_ID.get(r["position"], 3)].append(f(r["value"], 50))
                median_price = {p: sorted(v)[len(v) // 2] for p, v in by_pos.items()}

            actual, pos, preds, base_fpl, base_form, team_of = {}, {}, {}, {}, {}, {}
            seen = set()
            for r in rows[gw]:
                e = r["_el"]
                team_of[e] = r["_team"]
                actual[e] = actual.get(e, 0.0) + f(r["total_points"])
                if e in seen:
                    continue
                seen.add(e)
                p = POS_ID.get(r["position"], 3)
                pos[e] = p
                st = state.get(e) or {"pos": p, "mins": 0.0, "starts": 0.0, "xg": 0.0, "xa": 0.0, "dc": 0.0, "saves": 0.0, "bonus": 0.0, "xgc": 0.0, "pts": 0.0, "games": 0}
                st = dict(st, pos=p, team_games=team_games[r["_team"]], chance=1.0, status="a")
                code = int(f(raw.get(e, {}).get("code"), -1))
                prior = model.make_prior(p, f(r["value"], 50), median_price.get(p, 50), prior_by_code.get(code), P)
                fxs = fixtures_this.get(r["_team"], [])
                totals, _, _, _ = model.player_xp(st, prior, [fxs], lam.get(r["_team"], P["league_xgc"]), P)
                preds[e] = totals[0]
                base_fpl[e] = f(r.get("xP"))
                base_form[e] = st["pts"] / st["games"] if st["games"] else 0.0
            # Score only players with a fixture and any minutes so far, so the
            # comparison is over plausible picks rather than the whole database.
            ids = [e for e in preds if fixtures_this.get(team_of[e]) and state.get(e, {}).get("mins", 0) > 0]
            if len(ids) >= 50:
                sub = lambda d: {i: d[i] for i in ids}  # noqa: E731
                gw_res = {"gw": gw, "n": len(ids)}
                for name, d in (("model", preds), ("vaastav_xp", base_fpl), ("form", base_form)):
                    m = score(sub(d), sub(actual), pos)
                    results[name].append(m)
                    gw_res[name] = m
                per_gw.append(gw_res)

        # Fold this gameweek into state.
        played_teams = set()
        for x in fx_by_gw.get(gw, []):
            if x.get("finished") in ("True", "true", "1"):
                played_teams.add(int(x["team_h"])); played_teams.add(int(x["team_a"]))
        for t in played_teams:
            team_games[t] += 1
        for r in rows[gw]:
            e = r["_el"]
            p = POS_ID.get(r["position"], 3)
            st = state.setdefault(e, {"pos": p, "mins": 0.0, "starts": 0.0, "xg": 0.0, "xa": 0.0, "dc": 0.0, "saves": 0.0, "bonus": 0.0, "xgc": 0.0, "pts": 0.0, "games": 0})
            st["mins"] += f(r["minutes"]); st["starts"] += f(r.get("starts"))
            st["xg"] += f(r["expected_goals"]); st["xa"] += f(r["expected_assists"])
            st["dc"] += f(r.get("defensive_contribution")); st["saves"] += f(r["saves"])
            st["bonus"] += f(r["bonus"]); st["xgc"] += f(r["expected_goals_conceded"])
            st["pts"] += f(r["total_points"])
            if f(r["minutes"]) > 0:
                st["games"] += 1
            if p == 1:
                gk_by_team[r["_team"]].add(e)
    return results, per_gw


def summarise(results):
    out = {}
    for name, ms in results.items():
        n = len(ms)
        out[name] = {k: sum(m[k] for m in ms) / n for k in ms[0]} if n else {}
        out[name]["gameweeks"] = n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2025-26")
    ap.add_argument("--prior", default="2024-25", help="previous season used as the rate prior ('' for none)")
    ap.add_argument("--root", default="data/raw/seasons")
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--out", default="")
    ap.add_argument("--first", type=int, default=2)
    ap.add_argument("--last", type=int, default=38)
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE", help="override a model parameter")
    a = ap.parse_args()
    params = dict(model.PARAMS)
    for kv in a.set:
        k, v = kv.split("=", 1)
        if k not in params:
            sys.exit(f"unknown parameter {k}; known: {', '.join(params)}")
        params[k] = float(v)
    if a.fetch:
        fetch(a.season, a.root)
        if a.prior:
            fetch(a.prior, a.root)
    rows, fx, teams, raw = load_season(a.root, a.season)
    prior = load_prior(a.root, a.prior)
    results, per_gw = run(rows, fx, teams, raw, prior, params, a.first, a.last)
    summary = summarise(results)
    cols = ["spearman", "rmse", "xi", "captain_pts", "captain_top5", "top50"]
    changed = {k: v for k, v in params.items() if v != model.PARAMS[k]}
    print(f"\n{a.season}: {summary['model']['gameweeks']} gameweeks scored, prior {a.prior or 'none'}" + (f", overrides {changed}" if changed else "") + "\n")
    print(f"{'variant':10}" + "".join(f"{c:>13}" for c in cols))
    for name in ("model", "vaastav_xp", "form"):
        s = summary[name]
        print(f"{name:10}" + "".join(f"{s[c]:13.3f}" for c in cols))
    print("\nvaastav_xp: the dataset's own expected-points column; form: season points per game")
    print("spearman: rank correlation of predicted vs actual (higher is better)")
    print("rmse: points error per player; xi: actual points of the predicted best 1-4-4-2")
    print("captain_pts: actual points of the predicted top player; captain_top5: how often that player was in the actual top 5")
    print("top50: mean actual points of the 50 highest predictions")
    if a.out:
        os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
        with open(a.out, "w", encoding="utf-8") as fh:
            json.dump({"season": a.season, "prior": a.prior, "params": params, "summary": summary, "per_gw": per_gw}, fh, separators=(",", ":"))
        print(f"\nwrote {a.out}")


if __name__ == "__main__":
    main()
