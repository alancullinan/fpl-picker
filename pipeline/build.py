#!/usr/bin/env python3
"""Turn data/raw/*.json into the single bundle the site reads: data/fpl.json.

Also computes a transparent expected-points model (xp1 = next gameweek,
xp5 = next five). The model is deliberately simple and every input is in
the bundle so the numbers can be checked by hand. See .claude/skills/fpl/SKILL.md
for the scoring rules it encodes.
"""
import argparse
import csv
import json
import math
import os
from datetime import datetime, timezone

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_PTS = {1: 10, 2: 6, 3: 5, 4: 4}
CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}
ASSIST_PTS = 3
DEFCON_THRESHOLD = {2: 10, 3: 12, 4: 12}
LEAGUE_XGC_PRIOR = 1.35      # goals conceded per game, league average
PRIOR_GAMES = 5              # weight of the prior vs observed team xGC
HORIZON = 8                  # gameweeks of fixtures carried per team
PRIOR_MINUTES = 900          # this season's per-90 rates are shrunk towards a prior
                             # worth ten full games; ~GW12 the data dominates
PREV_MIN_MINUTES = 450       # last season counts as a prior only above this
# Positional priors per 90 at the position's median price. Attacking rates
# scale with price because price is FPL's own prior on output.
PRIOR_XG = {1: 0.0, 2: 0.06, 3: 0.16, 4: 0.35}
PRIOR_XA = {1: 0.0, 2: 0.06, 3: 0.14, 4: 0.10}
PRIOR_DC = {1: 0.0, 2: 8.5, 3: 6.0, 4: 3.0}
PRIOR_SAVES = {1: 2.7, 2: 0.0, 3: 0.0, 4: 0.0}


def load(raw, name):
    path = os.path.join(raw, name)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def fnum(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_prev(raw):
    """Last season's totals keyed by player code (stable across seasons)."""
    path = os.path.join(raw, "prev-season.csv")
    prev = {}
    if not os.path.exists(path):
        return prev
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                prev[int(r["code"])] = r
            except (KeyError, ValueError):
                continue
    return prev


def shrink(obs_total, obs_minutes, prior_rate):
    """Per-90 rate blending observed totals with a prior worth PRIOR_MINUTES."""
    return (obs_total + prior_rate * PRIOR_MINUTES / 90.0) / ((obs_minutes + PRIOR_MINUTES) / 90.0)


def fdr_attack_mult(fdr, home):
    # FDR 3 is neutral. Easier fixtures lift attacking output, harder ones cut it.
    m = 1.0 + (3 - fdr) * 0.12
    return m * (1.05 if home else 0.95)


def fdr_concede_mult(fdr, home):
    m = 1.0 + (fdr - 3) * 0.15
    return m * (0.92 if home else 1.08)


def build(raw, out):
    bs = load(raw, "bootstrap-static.json")
    fixtures = load(raw, "fixtures.json") or []
    entry = load(raw, "entry.json")
    history = load(raw, "entry-history.json")
    transfers = load(raw, "entry-transfers.json") or []
    picks = load(raw, "entry-picks.json")

    events = bs["events"]
    teams = {t["id"]: t for t in bs["teams"]}
    current = next((e["id"] for e in events if e.get("is_current")), None)
    next_ev = next((e for e in events if e.get("is_next")), None)
    if next_ev is None and current is not None:
        next_ev = next((e for e in events if e["id"] == current + 1), None)
    next_id = next_ev["id"] if next_ev else None

    # Games played per team this season (finished fixtures).
    played = {tid: 0 for tid in teams}
    for f in fixtures:
        if f.get("finished"):
            played[f["team_h"]] += 1
            played[f["team_a"]] += 1

    # Upcoming fixtures per team, grouped by gameweek, from the next gameweek on.
    upcoming = {tid: {} for tid in teams}
    for f in fixtures:
        ev = f.get("event")
        if ev is None or next_id is None or ev < next_id or ev >= next_id + HORIZON:
            continue
        for side, opp, fdr_key in (("team_h", "team_a", "team_h_difficulty"), ("team_a", "team_h", "team_a_difficulty")):
            tid = f[side]
            upcoming[tid].setdefault(ev, []).append({
                "gw": ev,
                "opp": f[opp],
                "opp_short": teams[f[opp]]["short_name"],
                "home": side == "team_h",
                "fdr": f.get(fdr_key) or 3,
                "kickoff": f.get("kickoff_time"),
            })

    # Team defensive strength: xG conceded per game from the goalkeepers' xGC,
    # shrunk towards the league prior while the sample is small.
    team_xgc = {}
    for tid in teams:
        gks = [p for p in bs["elements"] if p["team"] == tid and p["element_type"] == 1 and fnum(p["minutes"]) > 0]
        obs_xgc = sum(fnum(p["expected_goals_conceded"]) for p in gks)
        obs_games = sum(fnum(p["minutes"]) for p in gks) / 90.0
        team_xgc[tid] = (obs_xgc + LEAGUE_XGC_PRIOR * PRIOR_GAMES) / (obs_games + PRIOR_GAMES)

    prev = load_prev(raw)
    median_price = {}
    for pos in POS:
        prices = sorted(e["now_cost"] for e in bs["elements"] if e["element_type"] == pos)
        median_price[pos] = prices[len(prices) // 2] if prices else 50

    players = []
    for p in bs["elements"]:
        pos = p["element_type"]
        mins = fnum(p["minutes"])
        team_games = max(played.get(p["team"], 0), 1)
        starts = fnum(p.get("starts"))

        # Priors: last season's rates when the sample is big enough, else a
        # positional rate scaled by price.
        scale = p["now_cost"] / median_price[pos]
        pr = prev.get(p["code"])
        pmin = fnum(pr.get("minutes")) if pr else 0.0
        if pr and pmin >= PREV_MIN_MINUTES:
            prior_xg = fnum(pr.get("expected_goals")) / pmin * 90
            prior_xa = fnum(pr.get("expected_assists")) / pmin * 90
            prior_dc = fnum(pr.get("defensive_contribution")) / pmin * 90
            prior_saves = fnum(pr.get("saves")) / pmin * 90
            prior_bonus = fnum(pr.get("bonus")) / (pmin / 90.0)
            prior_src = "prev"
        else:
            prior_xg = PRIOR_XG[pos] * scale ** 1.5
            prior_xa = PRIOR_XA[pos] * scale ** 1.2
            prior_dc = PRIOR_DC[pos]
            prior_saves = PRIOR_SAVES[pos]
            prior_bonus = min(1.0, 0.15 * scale ** 2)
            prior_src = "price"

        # Probability of playing next gameweek.
        chance = p.get("chance_of_playing_next_round")
        chance = 1.0 if chance in (None, "None", "") else fnum(chance) / 100.0
        if p["status"] in ("u", "n"):
            chance = 0.0
        if mins > 0:
            start_rate = min(1.0, starts / team_games)
            p_play = chance * max(0.15, 0.85 * start_rate + 0.15 * min(1.0, mins / (team_games * 90.0)))
            p_60 = chance * min(1.0, mins / (team_games * 90.0)) * 0.95
        else:
            p_play = chance * 0.15
            p_60 = p_play * 0.6

        xg90 = shrink(fnum(p.get("expected_goals")), mins, prior_xg)
        xa90 = shrink(fnum(p.get("expected_assists")), mins, prior_xa)
        dc90 = shrink(fnum(p.get("defensive_contribution")), mins, prior_dc)
        saves90 = shrink(fnum(p.get("saves")), mins, prior_saves)
        bonus_pg = shrink(fnum(p.get("bonus")), mins, prior_bonus)
        lam_team = team_xgc[p["team"]]

        # Points per full 90 in a neutral fixture, broken into components.
        def xp_for(fx):
            att = fdr_attack_mult(fx["fdr"], fx["home"])
            dfn = fdr_concede_mult(fx["fdr"], fx["home"])
            lam = lam_team * dfn
            p_cs = math.exp(-lam)
            appearance = 2.0 * p_60 + 1.0 * (p_play - p_60)
            attack = (xg90 * GOAL_PTS[pos] + xa90 * ASSIST_PTS) * att * p_play
            cs = CS_PTS[pos] * p_cs * p_60
            conceded = -(lam / 2.0) * p_60 if pos in (1, 2) else 0.0
            defcon = 0.0
            if pos in DEFCON_THRESHOLD and dc90 > 0:
                z = (dc90 - DEFCON_THRESHOLD[pos]) / 3.0
                defcon = 2.0 * (1.0 / (1.0 + math.exp(-z))) * p_60
            saves = (saves90 / 3.0) * p_60 * dfn if pos == 1 else 0.0
            bonus = bonus_pg * 0.7 * att * p_play
            return {"app": appearance, "att": attack, "cs": cs + conceded, "dc": defcon, "sv": saves, "bon": bonus}

        fx_list = upcoming.get(p["team"], {})
        gw_xp = []
        parts1 = {}
        for gw in range(next_id, next_id + HORIZON) if next_id else []:
            total = 0.0
            for fx in fx_list.get(gw, []):
                parts = xp_for(fx)
                total += sum(parts.values())
                if gw == next_id:
                    for k, v in parts.items():
                        parts1[k] = round(parts1.get(k, 0.0) + v, 2)
            gw_xp.append(round(total, 2))
        xp1 = gw_xp[0] if gw_xp else 0.0
        xp5 = round(sum(gw_xp[:5]), 2)

        players.append({
            "id": p["id"],
            "code": p["code"],
            "name": p["web_name"],
            "full": f"{p['first_name']} {p['second_name']}".strip(),
            "team": p["team"],
            "pos": pos,
            "price": p["now_cost"] / 10.0,
            "sel": fnum(p["selected_by_percent"]),
            "status": p["status"],
            "news": p.get("news") or "",
            "chance": None if p.get("chance_of_playing_next_round") in (None, "None", "") else int(fnum(p["chance_of_playing_next_round"])),
            "form": fnum(p["form"]),
            "pts": int(fnum(p["total_points"])),
            "ppg": fnum(p.get("points_per_game")),
            "min": int(mins),
            "starts": int(starts),
            "g": int(fnum(p["goals_scored"])),
            "a": int(fnum(p["assists"])),
            "cs": int(fnum(p["clean_sheets"])),
            "bonus": int(fnum(p["bonus"])),
            "bps": int(fnum(p["bps"])),
            "xg": fnum(p.get("expected_goals")),
            "xa": fnum(p.get("expected_assists")),
            "xgi90": fnum(p.get("expected_goal_involvements_per_90")),
            "xgc90": fnum(p.get("expected_goals_conceded_per_90")),
            "dc90": fnum(p.get("defensive_contribution_per_90")),
            "saves90": fnum(p.get("saves_per_90")),
            "rates": {"xg90": round(xg90, 2), "xa90": round(xa90, 2), "dc90": round(dc90, 1),
                      "sv90": round(saves90, 1), "bon": round(bonus_pg, 2), "src": prior_src},
            "ict": fnum(p.get("ict_index")),
            "ep_next": fnum(p.get("ep_next")),
            "p_play": round(p_play, 2),
            "xp1": xp1,
            "xp5": xp5,
            "xp_gw": gw_xp,
            "parts": parts1,
            "tin": int(fnum(p.get("transfers_in_event"))),
            "tout": int(fnum(p.get("transfers_out_event"))),
            "dprice": fnum(p.get("cost_change_event")) / 10.0,
        })

    team_out = {}
    for tid, t in teams.items():
        fx = []
        for gw in range(next_id, next_id + HORIZON) if next_id else []:
            fx.append(upcoming[tid].get(gw, []))
        team_out[tid] = {
            "id": tid,
            "name": t["name"],
            "short": t["short_name"],
            "played": played.get(tid, 0),
            "xgc": round(team_xgc[tid], 2),
            "fixtures": fx,
        }

    chips_def = []
    for c in bs.get("chips", []) or []:
        chips_def.append({
            "name": c.get("name"),
            "number": c.get("number"),
            "start": c.get("start_event"),
            "stop": c.get("stop_event"),
        })

    my = None
    if entry:
        my = {
            "id": entry["id"],
            "manager": f"{entry.get('player_first_name','')} {entry.get('player_last_name','')}".strip(),
            "team_name": entry.get("name"),
            "overall_points": entry.get("summary_overall_points"),
            "overall_rank": entry.get("summary_overall_rank"),
            "gw_points": entry.get("summary_event_points"),
            "gw_rank": entry.get("summary_event_rank"),
            "bank": (entry.get("last_deadline_bank") or 0) / 10.0,
            "value": (entry.get("last_deadline_value") or 0) / 10.0,
            "picks": [],
            "chips_used": [],
            "free_transfers": None,
            "transfers": [],
        }
        if history:
            my["chips_used"] = [{"name": c["name"], "gw": c["event"]} for c in history.get("chips", [])]
            my["free_transfers"] = free_transfers(history)
            my["history"] = [{
                "gw": h["event"], "pts": h["points"], "rank": h.get("overall_rank"),
                "bank": h["bank"] / 10.0, "value": h["value"] / 10.0,
                "transfers": h.get("event_transfers", 0), "hit": h.get("event_transfers_cost", 0),
                "bench": h.get("points_on_bench", 0),
            } for h in history.get("current", [])]
        if picks:
            my["picks_gw"] = picks.get("_event")
            my["picks"] = [{
                "id": pk["element"], "slot": pk["position"], "mult": pk["multiplier"],
                "c": pk.get("is_captain", False), "vc": pk.get("is_vice_captain", False),
            } for pk in picks.get("picks", [])]
            eh = picks.get("entry_history") or {}
            if eh:
                my["bank"] = eh.get("bank", 0) / 10.0
                my["value"] = eh.get("value", 0) / 10.0
                my["gw_points"] = eh.get("points")
            my["active_chip"] = picks.get("active_chip")
        my["transfers"] = [{
            "gw": t["event"], "in": t["element_in"], "out": t["element_out"],
            "in_cost": t["element_in_cost"] / 10.0, "out_cost": t["element_out_cost"] / 10.0,
        } for t in sorted(transfers, key=lambda t: (t["event"], t["time"]))[-20:]]

    bundle = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "season": season_label(events),
        "current_gw": current,
        "next_gw": next_id,
        "deadline": next_ev.get("deadline_time") if next_ev else None,
        "events": [{"id": e["id"], "name": e["name"], "deadline": e["deadline_time"],
                    "finished": e.get("finished", False), "avg": e.get("average_entry_score"),
                    "top": e.get("highest_score")} for e in events],
        "chips": chips_def,
        "teams": team_out,
        "players": players,
        "me": my,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, separators=(",", ":"))
    print(f"wrote {out} ({os.path.getsize(out)//1024} KB): {len(players)} players, next GW {next_id}, "
          f"team {'ok' if my and my['picks'] else 'absent'}")


def free_transfers(history):
    """Free transfers available for the upcoming gameweek.

    The public API does not expose this, so replay the season: 1 FT for GW2,
    +1 per gameweek, capped at 5. A hit resets to 1. Wildcard and Free Hit
    keep the count you had and still accrue the +1.
    """
    chips = {c["event"]: c["name"] for c in history.get("chips", [])}
    rows = sorted(history.get("current", []), key=lambda r: r["event"])
    ft = 1
    for r in rows:
        gw = r["event"]
        if gw == 1:
            ft = 1
            continue
        used = r.get("event_transfers", 0)
        if chips.get(gw) in ("wildcard", "freehit"):
            ft = min(5, ft + 1)
        elif used > ft:
            ft = 1
        else:
            ft = min(5, ft - used + 1)
    return ft


def season_label(events):
    if not events:
        return ""
    y = int(events[0]["deadline_time"][:4])
    return f"{y}/{str(y + 1)[2:]}"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out", default="data/fpl.json")
    a = ap.parse_args()
    build(a.raw, a.out)
