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
    """Return [{home, away, status, kickoff, home_players, away_players, home_inj, away_inj}].

    Players are (full name, position) from the link title; injuries are
    (full name, status) with Rotowire's OUT/QUES/... tag.
    """
    matches = []
    blocks = re.split(r'<div class="lineup is-soccer', body)[1:]
    for blk in blocks:
        m = {"home": None, "away": None, "status": "expected", "kickoff": None,
             "home_players": [], "away_players": [], "home_inj": [], "away_inj": []}
        for side in ("home", "visit"):
            t = re.search(r'class="lineup__team is-%s[^>]*>.*?class="lineup__abbr[^"]*">\s*([A-Z]{2,4})\s*<' % side, blk, re.S)
            if t:
                m["home" if side == "home" else "away"] = t.group(1)
        t = re.search(r'class="lineup__time[^"]*">(.*?)</div>', blk, re.S)
        if t:
            m["kickoff"] = strip_tags(t.group(1))
        for side in ("visit", "home"):
            lst = re.search(r'<ul class="lineup__list is-%s[^"]*">(.*?)</ul>' % side, blk, re.S)
            if not lst:
                continue
            body_ul = lst.group(1)
            if re.search(r'lineup__status is-confirmed|Confirmed Lineup', body_ul):
                m["status"] = "confirmed"
            # The XI comes first; an "Injuries" title separates the injured list.
            parts = re.split(r'lineup__title[^>]*>\s*Injuries', body_ul, maxsplit=1)
            key = "away" if side == "visit" else "home"
            m[key + "_players"] = _players(parts[0])[:11]
            if len(parts) > 1:
                m[key + "_inj"] = _players(parts[1], inj=True)
        if m["home"] and m["away"] and (m["home_players"] or m["away_players"]):
            matches.append(m)
    return matches


def _players(fragment, inj=False):
    out = []
    for li in re.findall(r'<li class="lineup__player[^"]*"[^>]*>(.*?)</li>', fragment, re.S):
        a = re.search(r'<a[^>]*title="([^"]*)"[^>]*>(.*?)</a>', li, re.S)
        if not a:
            continue
        name = html.unescape(a.group(1)).strip() or strip_tags(a.group(2))
        if inj:
            st = re.search(r'class="lineup__inj[^"]*">\s*([A-Z]+)', li)
            out.append((name, st.group(1) if st else "OUT"))
        else:
            pos = re.search(r'class="lineup__pos[^"]*">\s*([A-Z]{1,3})\s*<', li)
            out.append((name, pos.group(1) if pos else ""))
    return out


_SPECIAL = str.maketrans({"ø": "o", "Ø": "O", "ß": "ss", "ı": "i", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "æ": "ae", "Æ": "AE", "œ": "oe", "þ": "th"})


def norm(s):
    s = unicodedata.normalize("NFKD", s.translate(_SPECIAL)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", s.lower().replace("-", " ")).strip()


def match_players(matches, bootstrap):
    """Attach FPL ids: full name, then surname within the club, then initial + surname."""
    teams = {t["id"]: t["short_name"] for t in bootstrap["teams"]}
    by_team = {}
    for e in bootstrap["elements"]:
        by_team.setdefault(teams[e["team"]], []).append(e)

    def find(name, short):
        cands = by_team.get(short, [])
        n = norm(name)
        parts = n.split()
        if not parts or not cands:
            return None
        full = [e for e in cands if norm(f"{e['first_name']} {e['second_name']}") == n or norm(e["web_name"]) == n]
        if len(full) == 1:
            return full[0]["id"]
        surname = parts[-1]
        sur = [e for e in cands if norm(e["second_name"]).split()[-1:] == [surname] or norm(e["web_name"]).split()[-1:] == [surname]]
        if len(sur) == 1:
            return sur[0]["id"]
        if len(parts) > 1:
            ini = [e for e in sur if norm(e["first_name"])[:1] == parts[0][:1]]
            if len(ini) == 1:
                return ini[0]["id"]
        # Last resort: every word of the Rotowire name appears in the FPL full name.
        loose = [e for e in cands if all(w in norm(f"{e['first_name']} {e['second_name']} {e['web_name']}").split() for w in parts)]
        if len(loose) == 1:
            return loose[0]["id"]
        return None

    out = []
    for m in matches:
        h, a = ABBR.get(m["home"], m["home"]), ABBR.get(m["away"], m["away"])
        rec = {"home": h, "away": a, "status": m["status"], "kickoff": m["kickoff"],
               "home_xi": [], "away_xi": [], "injuries": {}, "unmatched": []}
        for side, short in (("home", h), ("away", a)):
            if short not in by_team:
                rec["unmatched"].append(f"team:{m[side]}")
                continue
            for name, _pos in m[side + "_players"]:
                pid = find(name, short)
                if pid:
                    rec[side + "_xi"].append(pid)
                else:
                    rec["unmatched"].append(f"{short}:{name}")
            for name, st in m[side + "_inj"]:
                pid = find(name, short)
                if pid:
                    rec["injuries"][str(pid)] = st
        out.append(rec)
    return out


def write(out, raw):
    matched = sum(len(m["home_xi"]) + len(m["away_xi"]) for m in out)
    inj = sum(len(m["injuries"]) for m in out)
    unmatched = [u for m in out for u in m["unmatched"]]
    print(f"lineups: {len(out)} matches, {matched} starters and {inj} injuries matched; unmatched {len(unmatched)}: {unmatched[:40]}")
    with open(os.path.join(raw, "lineups.json"), "w", encoding="utf-8") as f:
        json.dump({"source": "rotowire", "fetched": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "matches": out}, f, separators=(",", ":"))


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
    write(match_players(matches, bs), a.raw)


if __name__ == "__main__":
    main()
