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
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import model  # noqa: E402
from model import PARAMS as P  # noqa: E402

POS = model.POS
HORIZON = 8                  # gameweeks of fixtures carried per team


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

    # Team defensive strength from the goalkeepers' xG conceded, shrunk to the league prior.
    team_xgc = {}
    for tid in teams:
        gks = [{"xgc": fnum(p["expected_goals_conceded"]), "mins": fnum(p["minutes"])}
               for p in bs["elements"] if p["team"] == tid and p["element_type"] == 1 and fnum(p["minutes"]) > 0]
        team_xgc[tid] = model.team_xgc(gks, P)

    prev = load_prev(raw)
    fixture_hist = load(raw, "element-history.json") or {}
    lineups = load(raw, "lineups.json") or {}
    short_to_id = {t["short_name"]: tid for tid, t in teams.items()}
    lineup_of, inj_tag, team_has_lineup, lineup_status = {}, {}, {}, {}
    for m in lineups.get("matches", []):
        for side in ("home", "away"):
            tid = short_to_id.get(m[side])
            if tid is None:
                continue
            team_has_lineup[tid] = True
            lineup_status[tid] = m.get("status", "expected")
            for pid in m[side + "_xi"]:
                lineup_of[pid] = "xi"
        for pid, tag in (m.get("injuries") or {}).items():
            inj_tag[int(pid)] = tag
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

        pr = prev.get(p["code"])
        prior = model.make_prior(pos, p["now_cost"], median_price[pos], pr, P,
                                 pen_order=p.get("penalties_order"),
                                 sp_order=p.get("corners_and_indirect_freekicks_order"))
        chance = p.get("chance_of_playing_next_round")
        state = {
            "pos": pos, "mins": mins, "starts": starts, "team_games": team_games,
            "chance": 1.0 if chance in (None, "None", "") else fnum(chance) / 100.0, "status": p["status"],
            "xg": fnum(p.get("expected_goals")), "xa": fnum(p.get("expected_assists")),
            "dc": fnum(p.get("defensive_contribution")), "saves": fnum(p.get("saves")), "bonus": fnum(p.get("bonus")),
        }
        # Most recent fixture first, as (minutes, started), for the minutes model.
        rows = fixture_hist.get(str(p["id"])) or []
        state["recent"] = [(r[1], bool(r[2])) for r in reversed(rows)][:12]
        lineup = lineup_of.get(p["id"]) or ("bench" if team_has_lineup.get(p["team"]) else None)
        state["lineup"] = lineup
        state["lineup_confirmed"] = lineup_status.get(p["team"]) == "confirmed"
        state["inj_tag"] = inj_tag.get(p["id"])
        fx_list = upcoming.get(p["team"], {})
        fixtures_by_gw = [fx_list.get(gw, []) for gw in range(next_id, next_id + HORIZON)] if next_id else []
        gw_xp, parts1, r, p_play = model.player_xp(state, prior, fixtures_by_gw, team_xgc[p["team"]], P)
        _, p_60, exp_frac = model.minutes_probs(dict(state, prior_start=prior.get("start", P["unseen_start"])), P)
        gw_full, _, _, _ = model.player_xp(state, prior, fixtures_by_gw, team_xgc[p["team"]], P, full=True)
        gw_full = [round(v, 2) for v in gw_full]
        gw_xp = [round(v, 2) for v in gw_xp]
        parts1 = {k: round(v, 2) for k, v in parts1.items()}
        xp1 = gw_xp[0] if gw_xp else 0.0
        xp5 = round(sum(gw_xp[:5]), 2)
        xg90, xa90, dc90, saves90, bonus_pg, prior_src = r["xg90"], r["xa90"], r["dc90"], r["sv90"], r["bon"], r["src"]

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
            "ev_pts": int(fnum(p.get("event_points"))),
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
            "p_60": round(p_60, 2),
            "xmin": int(round(exp_frac * 90)),
            "recent": [int(r[1]) for r in rows[-6:]],
            "pen": model._order(p.get("penalties_order")) or None,
            "sp": model._order(p.get("corners_and_indirect_freekicks_order")) or None,
            "fk": model._order(p.get("direct_freekicks_order")) or None,
            "lineup": lineup,
            "inj_tag": inj_tag.get(p["id"]),
            "xp1": xp1,
            "xp5": xp5,
            "xp_gw": gw_xp,
            "xp_full": gw_full,
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

    # The history endpoint occasionally returns no gameweek rows mid-season.
    # Rather than replay nothing and guess, reuse the previous bundle's entry data.
    prev_bundle = None
    if os.path.exists(out):
        try:
            with open(out, encoding="utf-8") as f:
                prev_bundle = json.load(f)
        except (OSError, ValueError):
            prev_bundle = None
    season_started = current is not None
    if season_started and history is not None and not history.get("current"):
        pm = (prev_bundle or {}).get("me") or {}
        if pm.get("history"):
            print("entry history came back empty; carrying forward the previous bundle's history", file=sys.stderr)
            history = {"current": [{"event": h["gw"], "points": h["pts"], "overall_rank": h.get("rank"),
                                    "bank": int(round(h["bank"] * 10)), "value": int(round(h["value"] * 10)),
                                    "event_transfers": h["transfers"], "event_transfers_cost": h["hit"],
                                    "points_on_bench": h["bench"]} for h in pm["history"]],
                       "chips": [{"name": c["name"], "event": c["gw"]} for c in pm.get("chips_used", [])]}
            if not transfers and pm.get("transfers"):
                transfers = [{"event": t["gw"], "element_in": t["in"], "element_out": t["out"],
                              "element_in_cost": int(round(t["in_cost"] * 10)), "element_out_cost": int(round(t["out_cost"] * 10)),
                              "time": ""} for t in pm["transfers"]]
        else:
            history = None
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
        "lineups": {"source": lineups.get("source"), "fetched": lineups.get("fetched"),
                    "matches": [{"home": m["home"], "away": m["away"], "status": m["status"]} for m in lineups.get("matches", [])]} if lineups else None,
        "teams": team_out,
        "players": players,
        "me": my,
    }
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(bundle, f, separators=(",", ":"))
    snapshot(bundle, os.path.join(os.path.dirname(out) or ".", "history"))
    print(f"wrote {out} ({os.path.getsize(out)//1024} KB): {len(players)} players, next GW {next_id}, "
          f"team {'ok' if my and my['picks'] else 'absent'}")


def snapshot(bundle, hist_dir):
    """Record this run's next-gameweek predictions so they can be scored later.

    One file per gameweek, overwritten on every run until the deadline passes,
    after which next_gw moves on and the file is frozen.
    """
    gw = bundle.get("next_gw")
    if not gw:
        return
    os.makedirs(hist_dir, exist_ok=True)
    path = os.path.join(hist_dir, f"gw{gw:02d}.json")
    data = {
        "gw": gw, "generated": bundle["generated"],
        "players": {str(p["id"]): [p["xp1"], p["p_play"], p["ep_next"]] for p in bundle["players"]},
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))
    print(f"wrote {path}")


def free_transfers(history):
    """Free transfers available for the upcoming gameweek.

    The public API does not expose this, so replay the season: 1 FT for GW2,
    +1 per gameweek, capped at 5. A hit resets to 1. Wildcard and Free Hit
    keep the count you had and still accrue the +1.
    """
    chips = {c["event"]: c["name"] for c in history.get("chips", [])}
    rows = sorted(history.get("current", []), key=lambda r: r["event"])
    if not rows:
        return None  # nothing to replay; the site shows "?" rather than a guess
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
