#!/usr/bin/env python3
"""Predicted lineups from Rotowire, matched to FPL player ids.

Writes data/raw/lineups.json:
  {"source": "rotowire", "fetched": iso, "matches": [
     {"home": "MCI", "away": "COV", "kickoff": "...", "status": "expected"|"confirmed",
      "home_xi": [fpl_id, ...], "away_xi": [...], "unmatched": ["name", ...]}]}

Best effort: any failure leaves no file and the model runs without lineups.
Standard library only. Rotowire's markup is not an API and will change; the
parser is deliberately loose and reports what it could not match.
"""
import argparse
import html
import json
import os
import re
import sys
import unicodedata
import urllib.request
from datetime import datetime, timezone

URL = "https://www.rotowire.com/soccer/lineups.php"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

# Rotowire abbreviations that differ from FPL short names.
ABBR = {"MCI": "MCI", "MUN": "MUN", "TOT": "TOT", "NEW": "NEW", "NOT": "NFO", "NFO": "NFO", "WHU": "WHU",
        "WOL": "WOL", "BHA": "BHA", "BRE": "BRE", "BOU": "BOU", "CRY": "CRY", "AVL": "AVL", "LEI": "LEI",
        "SOU": "SOU", "IPS": "IPS", "LEE": "LEE", "BUR": "BUR", "SUN": "SUN", "EVE": "EVE", "FUL": "FUL",
        "LIV": "LIV", "ARS": "ARS", "CHE": "CHE", "COV": "COV", "HUL": "HUL", "SHU": "SHU", "NOR": "NOR",
        "WBA": "WBA", "MID": "MID", "LUT": "LUT", "WAT": "WAT", "STK": "STK", "SWA": "SWA", "CAR": "CAR"}


def fetch(out_html):
    req = urllib.request.Request(URL, headers={"User-Agent": UA, "Accept-Language": "en-GB,en;q=0.8"})
    with urllib.request.urlopen(req, timeout=40) as r:
        body = r.read().decode("utf-8", "replace")
    os.makedirs(os.path.dirname(out_html) or ".", exist_ok=True)
    with open(out_html, "w", encoding="utf-8") as f:
        f.write(body)
    print(f"fetched {len(body)//1024} KB to {out_html}")
    return body


def strip_tags(s):
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def parse(body):
    """Return [{home, away, status, kickoff, home_players:[(name,pos)], away_players:[...]}]."""
    matches = []
    # Each match sits in a <div class="lineup is-soccer ..."> block; split on those.
    blocks = re.split(r'<div class="lineup is-soccer', body)[1:]
    for blk in blocks:
        m = {"home": None, "away": None, "status": "expected", "kickoff": None, "home_players": [], "away_players": []}
        abbrs = re.findall(r'class="lineup__abbr[^"]*">\s*([A-Z]{2,4})\s*<', blk)
        if len(abbrs) >= 2:
            m["away"], m["home"] = abbrs[0], abbrs[1]  # Rotowire lists visitor first
        t = re.search(r'class="lineup__time[^"]*">(.*?)</div>', blk, re.S)
        if t:
            m["kickoff"] = strip_tags(t.group(1))
        if re.search(r'is-confirmed|Confirmed Lineup', blk):
            m["status"] = "confirmed"
        for side in ("visit", "home"):
            lst = re.search(r'<ul class="lineup__list is-%s[^"]*">(.*?)</ul>' % side, blk, re.S)
            if not lst:
                continue
            players = []
            for li in re.findall(r'<li class="lineup__player[^"]*"[^>]*>(.*?)</li>', lst.group(1), re.S):
                pos = re.search(r'class="lineup__pos[^"]*">\s*([A-Z]{1,3})\s*<', li)
                name = re.search(r'<a[^>]*>(.*?)</a>', li, re.S)
                if name:
                    players.append((strip_tags(name.group(1)), pos.group(1) if pos else ""))
            m["away_players" if side == "visit" else "home_players"] = players[:11]
        if m["home"] and m["away"] and (m["home_players"] or m["away_players"]):
            matches.append(m)
    return matches


def norm(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower()).strip()


def match_players(matches, bootstrap):
    """Attach FPL ids using surname + team, then initial + surname, then web_name."""
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    by_team = {}
    for e in bootstrap["elements"]:
        by_team.setdefault(teams[e["team"]], []).append(e)

    def find(name, short):
        cands = by_team.get(short, [])
        n = norm(name)
        parts = n.split()
        if not parts:
            return None
        surname = parts[-1]
        initial = parts[0][0] if len(parts) > 1 else None
        exact = [e for e in cands if norm(e["web_name"]) == n or norm(f"{e['first_name']} {e['second_name']}") == n]
        if len(exact) == 1:
            return exact[0]["id"]
        sur = [e for e in cands if norm(e["second_name"]).split()[-1:] == [surname] or norm(e["web_name"]).split()[-1:] == [surname]]
        if len(sur) == 1:
            return sur[0]["id"]
        if initial:
            ini = [e for e in sur if norm(e["first_name"])[:1] == initial]
            if len(ini) == 1:
                return ini[0]["id"]
        loose = [e for e in cands if surname in norm(e["web_name"]) or surname in norm(e["second_name"])]
        if len(loose) == 1:
            return loose[0]["id"]
        return None

    out = []
    for m in matches:
        h, a = ABBR.get(m["home"], m["home"]), ABBR.get(m["away"], m["away"])
        rec = {"home": h, "away": a, "status": m["status"], "kickoff": m["kickoff"], "home_xi": [], "away_xi": [], "unmatched": []}
        for side, short in (("home", h), ("away", a)):
            for name, _pos in m[side + "_players"]:
                pid = find(name, short)
                if pid:
                    rec[side + "_xi"].append(pid)
                else:
                    rec["unmatched"].append(f"{short}:{name}")
        out.append(rec)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--html", default="", help="parse a saved page instead of fetching")
    ap.add_argument("--fetch-only", action="store_true")
    a = ap.parse_args()
    html_path = a.html or os.path.join(a.raw, "lineups.html")
    try:
        body = open(html_path, encoding="utf-8").read() if a.html else fetch(html_path)
    except Exception as e:  # noqa: BLE001
        print(f"lineups unavailable: {e}", file=sys.stderr)
        return
    if a.fetch_only:
        return
    matches = parse(body)
    print(f"parsed {len(matches)} matches: " + ", ".join(f"{m['away']}@{m['home']} ({len(m['away_players'])}/{len(m['home_players'])}, {m['status']})" for m in matches))
    bs_path = os.path.join(a.raw, "bootstrap-static.json")
    if not os.path.exists(bs_path):
        print("no bootstrap-static.json; skipping id matching")
        return
    with open(bs_path, encoding="utf-8") as f:
        bs = json.load(f)
    out = match_players(matches, bs)
    matched = sum(len(m["home_xi"]) + len(m["away_xi"]) for m in out)
    unmatched = [u for m in out for u in m["unmatched"]]
    print(f"matched {matched} players; unmatched {len(unmatched)}: {unmatched[:30]}")
    with open(os.path.join(a.raw, "lineups.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "rotowire", "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "matches": out}, f, separators=(",", ":"))


if __name__ == "__main__":
    main()
