#!/usr/bin/env python3
"""Read team news the numeric model cannot see, and turn it into player signals.

The measured headroom in this model is minutes: knowing who actually plays is
worth about six points a gameweek of best XI (backtest.py --oracle minutes).
Minutes are decided by things that only exist as prose - a manager's press
conference, a midweek European tie, a new signing settling in, a player moved
to a different role. This asks Claude, with web search, to read that and return
one structured signal per player.

Writes data/news/gwNN.json, one file per gameweek so the signals can later be
scored against what actually happened. Signals are recorded and shown; they do NOT feed
expected points until they have been scored against what actually happened
(see "Evaluating" in the FPL skill). Optional and best effort: a failure
leaves the previous file in place and never blocks a data refresh.

  python3 pipeline/news.py --dry-run     # print the prompt, call nothing
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lineups as lu  # noqa: E402  - reuse its name-to-FPL-id matcher

MODEL = "claude-opus-5"
MAX_TOKENS = 32000
MAX_SEARCHES = 18

SIGNALS = ["expected_to_start", "rotation_risk", "doubt", "out", "returning", "role_change"]

SCHEMA = {
    "type": "object",
    "properties": {
        "checked": {"type": "array", "items": {"type": "string"},
                    "description": "Club short names you found usable news for."},
        "signals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "player": {"type": "string", "description": "Player name as commonly written."},
                    "team": {"type": "string", "description": "Club short name from the list given."},
                    "signal": {"type": "string", "enum": SIGNALS},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                    "note": {"type": "string", "description": "One short sentence of what was actually said or reported."},
                    "source": {"type": "string", "description": "Publication or outlet, and the date if known."},
                },
                "required": ["player", "team", "signal", "confidence", "note", "source"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["checked", "signals"],
    "additionalProperties": False,
}

SYSTEM = """You are gathering Premier League team news for a Fantasy Premier League model that can count but cannot read.

The model already knows every player's minutes history, injury flag as published by FPL, and expected goals. Do not tell it those. Report only what is in the news and NOT in that data:

- a manager saying who will or will not start, or that someone is being managed or rested
- a European or cup tie close to this fixture that implies rotation
- a player returning to training or to the squad after injury, and when he is expected back
- a change of role or position that changes what he will score (a midfielder pushed forward, a full-back moved inside)
- a suspension, a new signing expected to go straight in, a manager change

Rules:
- One signal per player, for the player most affected. Do not list a whole squad.
- Only report what a named source actually said or reported. If you cannot find real news for a club, leave it out of "checked" rather than guessing.
- "expected_to_start" is for a player whose start is newly confirmed or newly in question and now resolved - not for every obvious regular.
- Prefer the most recent reporting. Football news goes stale in days.
- Confidence: high for a direct managerial quote, medium for consistent reporting, low for speculation.
- Be brief. The note is one sentence.
- If there is genuinely little news, return few signals. A short honest list is worth more than a long invented one."""


def build_prompt(d):
    teams = d["teams"]
    next_gw, deadline = d["next_gw"], d["deadline"]
    playing = []
    for tid, t in teams.items():
        fx = (t["fixtures"] or [[]])[0]
        for f in fx:
            playing.append(f"{t['short']} ({t['name']}) v {f['opp_short']}{' at home' if f['home'] else ' away'}")
    flagged = [p for p in d["players"]
               if (p["news"] or p["status"] != "a") and p["sel"] >= 1.5]
    flagged.sort(key=lambda p: -p["sel"])
    lines = [
        f"Gameweek {next_gw}. The deadline is {deadline}. Today is {datetime.now(timezone.utc):%d %B %Y}.",
        "",
        "Fixtures in this gameweek:",
    ] + [f"  {x}" for x in sorted(set(playing))] + [
        "",
        "FPL already publishes these flags, so do not repeat them - but news that CHANGES one of them is valuable:",
    ]
    for p in flagged[:25]:
        lines.append(f"  {p['name']} ({teams[str(p['team'])]['short']}): {p['status']}"
                     + (f" {p['chance']}%" if p["chance"] is not None else "")
                     + (f" - {p['news']}" if p["news"] else ""))
    lines += [
        "",
        "Search for the latest team news for these clubs and report what the model cannot see.",
        "Use the club short names above in the 'team' field.",
    ]
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/fpl.json")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--out-dir", default="data/news")
    ap.add_argument("--max-hours", type=float, default=96.0,
                    help="skip unless the deadline is within this many hours (news goes stale fast, and searching costs money)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    with open(a.data, encoding="utf-8") as f:
        d = json.load(f)
    if not d.get("next_gw"):
        print("no upcoming gameweek; skipping news", file=sys.stderr)
        return
    hours = (datetime.fromisoformat(d["deadline"].replace("Z", "+00:00")) - datetime.now(timezone.utc)).total_seconds() / 3600
    if hours > a.max_hours:
        print(f"deadline is {hours:.0f} h away (limit {a.max_hours:.0f}); skipping news", file=sys.stderr)
        return

    prompt = build_prompt(d)
    if a.dry_run:
        print(f"--- SYSTEM ---\n{SYSTEM}\n\n--- USER ({len(prompt):,} chars) ---\n{prompt}")
        return

    try:
        import anthropic
    except ImportError:
        print("the `anthropic` package is not installed; skipping news", file=sys.stderr)
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; skipping news", file=sys.stderr)
        return

    client = anthropic.Anthropic()
    try:
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            tools=[{"type": "web_search_20260209", "name": "web_search", "max_uses": MAX_SEARCHES}],
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            resp = stream.get_final_message()
    except Exception as e:  # noqa: BLE001 - never block a data refresh
        print(f"news failed: {type(e).__name__}: {e}", file=sys.stderr)
        return
    if resp.stop_reason in ("refusal", "max_tokens"):
        print(f"news stopped early: {resp.stop_reason}", file=sys.stderr)
        return

    text = next((b.text for b in reversed(resp.content) if b.type == "text"), None)
    try:
        parsed = json.loads(text) if text else None
    except json.JSONDecodeError as e:
        print(f"news was not valid JSON ({e})", file=sys.stderr)
        return
    if not parsed:
        print("no JSON in the news response", file=sys.stderr)
        return

    # Match reported names to FPL ids, reusing the lineup matcher.
    bs_path = os.path.join(a.raw, "bootstrap-static.json")
    matched, unmatched = [], []
    if os.path.exists(bs_path):
        with open(bs_path, encoding="utf-8") as f:
            bs = json.load(f)
        finder = _make_finder(bs)
        for sig in parsed.get("signals", []):
            pid = finder(sig["player"], sig["team"])
            if pid:
                matched.append(dict(sig, id=pid))
            else:
                unmatched.append(f"{sig['team']}:{sig['player']}")

    u = resp.usage
    cost = (u.input_tokens * 5 + u.output_tokens * 25) / 1e6
    payload = {
        "gw": d["next_gw"], "deadline": d["deadline"],
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_generated": d["generated"], "model": MODEL,
        "usage": {"input": u.input_tokens, "output": u.output_tokens, "cost_usd": round(cost, 4)},
        "checked": parsed.get("checked", []),
        "signals": matched, "unmatched": unmatched,
    }
    os.makedirs(a.out_dir, exist_ok=True)
    out = os.path.join(a.out_dir, f"gw{d['next_gw']:02d}.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"wrote {out}: {len(matched)} signals across {len(payload['checked'])} clubs, "
          f"{len(unmatched)} unmatched, about ${cost:.3f}")
    for s in matched[:12]:
        print(f"  {s['team']:4} {s['player'][:22]:22} {s['signal']:18} {s['confidence']:6} {s['note'][:60]}")


def _make_finder(bs):
    """lineups.match_players does whole fixtures; here we need one name at a time."""
    teams = {t["id"]: t["short_name"] for t in bs["teams"]}
    by_team = {}
    for e in bs["elements"]:
        by_team.setdefault(teams[e["team"]], []).append(e)

    def find(name, short):
        cands = by_team.get(lu.ABBR.get(short, short), [])
        n = lu.norm(name)
        parts = n.split()
        if not parts or not cands:
            return None
        full = [e for e in cands if lu.norm(f"{e['first_name']} {e['second_name']}") == n or lu.norm(e["web_name"]) == n]
        if len(full) == 1:
            return full[0]["id"]
        sur = [e for e in cands if lu.norm(e["second_name"]).split()[-1:] == parts[-1:]
               or lu.norm(e["web_name"]).split()[-1:] == parts[-1:]]
        if len(sur) == 1:
            return sur[0]["id"]
        if len(parts) > 1:
            ini = [e for e in sur if lu.norm(e["first_name"])[:1] == parts[0][:1]]
            if len(ini) == 1:
                return ini[0]["id"]
        return None
    return find


if __name__ == "__main__":
    main()
