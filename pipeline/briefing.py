#!/usr/bin/env python3
"""Write the weekly briefing: what to actually do with the team, in words.

The numeric model is good at what can be counted. This step hands the whole
picture - squad, expected points, ownership among strong managers, evidence
confidence, the solver's plan, chips and free transfers - to Claude and asks
for the judgement the numbers cannot make on their own: whether a two-point
gain is worth a differential risk, whether this is the week for a chip, which
of the model's own suggestions rest on too little football.

The rules it must respect come from .claude/skills/fpl/SKILL.md, so the
briefing and the rest of the project cannot drift apart.

Writes data/briefing.json. Optional: any failure leaves the previous briefing
in place and never blocks the data refresh. Needs ANTHROPIC_API_KEY and the
`anthropic` package (the rest of the pipeline stays standard-library only).

  python3 pipeline/briefing.py --dry-run    # print the prompt, call nothing
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

MODEL = "claude-opus-5"   # advice only; nothing here feeds expected points
TOP_PLAYERS = 60          # candidates offered beyond the user's own squad
POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string", "description": "One sentence: the single most important thing this week."},
        "do": {
            "type": "array", "description": "Actions worth taking now. May be empty.",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "The action, in the imperative."},
                    "why": {"type": "string", "description": "The reasoning, quoting the numbers that justify it."},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["action", "why", "confidence"], "additionalProperties": False,
            },
        },
        "consider": {
            "type": "array", "description": "Genuine judgement calls, with both sides put.",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "why": {"type": "string", "description": "The case for and the case against."},
                    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
                },
                "required": ["action", "why", "confidence"], "additionalProperties": False,
            },
        },
        "ignore": {
            "type": "array", "description": "Things the numbers flag that should NOT be acted on, and why.",
            "items": {
                "type": "object",
                "properties": {
                    "action": {"type": "string"},
                    "why": {"type": "string"},
                },
                "required": ["action", "why"], "additionalProperties": False,
            },
        },
        "captain": {
            "type": "object",
            "properties": {
                "pick": {"type": "string"}, "alternative": {"type": "string"},
                "why": {"type": "string", "description": "Include ownership: captaining differently to the field is a rank decision."},
            },
            "required": ["pick", "alternative", "why"], "additionalProperties": False,
        },
        "chips": {"type": "string", "description": "Chip advice, or plainly that no chip is in play this week."},
        "risks": {"type": "array", "description": "What could make this advice wrong.", "items": {"type": "string"}},
    },
    "required": ["headline", "do", "consider", "ignore", "captain", "chips", "risks"],
    "additionalProperties": False,
}

SYSTEM = """You are advising one manager on their Fantasy Premier League team. You are not writing content: you are helping a specific person make three decisions before a deadline - transfers, captain, and whether to play a chip.

The rules of the game, the model that produced these numbers, and its known weaknesses are given below. Follow them exactly. They are the same rules the rest of this project runs on.

How to think:
- The numbers handle what can be counted. Your job is the judgement they cannot make: whether a small expected gain is worth a differential risk, whether a suggestion rests on too little evidence, when to bank a transfer rather than spend it.
- Ownership among strong managers is a RANK decision, not a points one. A player owned by most of the top 10,000 is a risk you carry by not owning him, whatever his expected points. Say so in those terms.
- Treat a low-confidence player as what he is: an extrapolation from a few hundred minutes. Name the minutes when you recommend or reject one.
- Be willing to say "do nothing". Banking a free transfer is often correct and rarely said.
- Never invent a number. Every figure you quote must appear in the data you were given.
- Write plainly, in British English, to someone who knows the game. No hype, no filler, no restating the numbers back without adding a judgement."""


def fnum(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def player_line(p, teams, horizon=5):
    t = teams[str(p["team"])]
    fx = []
    for g in t["fixtures"][:horizon]:
        fx.append("/".join(f"{f['opp_short']}{'(H)' if f['home'] else '(A)'}{f['fdr']}" for f in g) if g else "blank")
    bits = [
        f"{p['name']} ({t['short']} {POS[p['pos']]}) £{p['price']}m",
        f"xP next {p['xp1']}, next5 {round(sum(p['xp_gw'][:5]), 1)}",
        f"mins {p['xmin']}/90 ({int(p['p_play'] * 100)}% play)",
        f"own {p['sel']}%" + (f", top10k {p['town']}%" if p.get("town") is not None else ""),
    ]
    if p.get("tcap"):
        bits.append(f"captained by {p['tcap']}% of the top")
    if p.get("conf") == "low":
        bits.append(f"THIN EVIDENCE: {p['ev']} min of it, {p['min']} this season")
    if p.get("lineup") == "xi":
        bits.append("in predicted XI")
    elif p.get("lineup") == "bench":
        bits.append("NOT in predicted XI")
    if p.get("inj_tag"):
        bits.append(f"tagged {p['inj_tag']}")
    if p.get("news"):
        bits.append(f"news: {p['news']}")
    if p.get("pen") == 1:
        bits.append("first on penalties")
    bits.append("fixtures " + ", ".join(fx))
    return " | ".join(bits)


def build_context(d):
    teams = d["teams"]
    by = {p["id"]: p for p in d["players"]}
    me = d.get("me") or {}
    out = [
        f"DEADLINE: gameweek {d['next_gw']}, {d['deadline']}. Data generated {d['generated']}.",
        "",
        "YOUR SITUATION",
        f"  bank £{me.get('bank')}m, squad value £{me.get('value')}m, free transfers {me.get('free_transfers')}",
        f"  overall rank {me.get('overall_rank'):,}" if me.get("overall_rank") else "",
        "  chips already used: " + (", ".join(f"{c['name']} in GW{c['gw']}" for c in me.get("chips_used") or []) or "none"),
        f"  chips available now: " + ", ".join(
            c["name"] for c in d.get("chips", [])
            if d["next_gw"] >= c["start"] and d["next_gw"] <= c["stop"]
            and not any(u["name"] == c["name"] and c["start"] <= u["gw"] <= c["stop"] for u in (me.get("chips_used") or []))
        ),
        "",
        "YOUR SQUAD (starting XI first, then bench in order)",
    ]
    picks = sorted([pk for pk in me.get("picks", []) if pk["id"] in by], key=lambda x: x["slot"])
    for pk in picks:
        role = "C" if pk.get("c") else ("V" if pk.get("vc") else "")
        where = "XI" if pk["slot"] <= 11 else f"BENCH{pk['slot'] - 11}"
        out.append(f"  [{where}{' ' + role if role else ''}] " + player_line(by[pk["id"]], teams))
    mine = {pk["id"] for pk in picks}

    out += ["", f"BEST AVAILABLE PLAYERS NOT IN YOUR SQUAD (top {TOP_PLAYERS} by expected points over 5 gameweeks)"]
    others = sorted((p for p in d["players"] if p["id"] not in mine and p["p_play"] > 0.5),
                    key=lambda p: -sum(p["xp_gw"][:5]))[:TOP_PLAYERS]
    for p in others:
        out.append("  " + player_line(p, teams))

    if d.get("top"):
        out += ["", f"OWNERSHIP CONTEXT: sampled {d['top']['sampled']} squads from the top {d['top']['ranks'][1]:,} after gameweek {d['top']['gw']}."]
    if d.get("lineups"):
        out += [f"PREDICTED LINEUPS: {len(d['lineups']['matches'])} matches from Rotowire, fetched {d['lineups']['fetched']}. A player marked 'NOT in predicted XI' may simply be missing from a match not yet published."]
    return "\n".join(x for x in out if x != "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/fpl.json")
    ap.add_argument("--out", default="data/briefing.json")
    ap.add_argument("--rules", default=".claude/skills/fpl/SKILL.md")
    ap.add_argument("--dry-run", action="store_true", help="print the prompt and exit without calling the API")
    a = ap.parse_args()

    with open(a.data, encoding="utf-8") as f:
        d = json.load(f)
    if not (d.get("me") or {}).get("picks"):
        print("no squad in the bundle; skipping briefing", file=sys.stderr)
        return
    rules = ""
    if os.path.exists(a.rules):
        with open(a.rules, encoding="utf-8") as f:
            rules = f.read()

    context = build_context(d)
    system = SYSTEM + "\n\n=== THE RULES AND THE MODEL ===\n" + rules
    user = context + "\n\nWrite the briefing for this deadline."

    if a.dry_run:
        print(f"--- SYSTEM ({len(system):,} chars) ---\n{system[:1200]}\n...\n")
        print(f"--- USER ({len(user):,} chars) ---\n{user}")
        return

    try:
        import anthropic
    except ImportError:
        print("the `anthropic` package is not installed; skipping briefing", file=sys.stderr)
        return
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set; skipping briefing", file=sys.stderr)
        return

    client = anthropic.Anthropic()
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=8000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
            system=system,
            messages=[{"role": "user", "content": user}],
        )
    except Exception as e:  # noqa: BLE001 - the briefing must never block a data refresh
        print(f"briefing failed: {type(e).__name__}: {e}", file=sys.stderr)
        return
    if resp.stop_reason == "refusal":
        print(f"briefing refused: {getattr(resp, 'stop_details', None)}", file=sys.stderr)
        return

    text = next((b.text for b in resp.content if b.type == "text"), None)
    if not text:
        print("no text block in the response; skipping", file=sys.stderr)
        return
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        print(f"response was not valid JSON: {e}", file=sys.stderr)
        return

    u = resp.usage
    cost = (u.input_tokens * 5 + u.output_tokens * 25) / 1e6
    payload = {
        "gw": d["next_gw"], "deadline": d["deadline"],
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_generated": d["generated"], "model": MODEL,
        "usage": {"input": u.input_tokens, "output": u.output_tokens, "cost_usd": round(cost, 4)},
        "briefing": parsed,
    }
    os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=1)
    print(f"wrote {a.out}: {u.input_tokens:,} in / {u.output_tokens:,} out, about ${cost:.3f}")
    print("headline:", parsed.get("headline"))


if __name__ == "__main__":
    main()
