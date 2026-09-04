#!/usr/bin/env python3
"""Fetch raw data from the FPL API into data/raw/.

Standard library only so it runs on a bare GitHub Actions runner.

Files written (all raw API responses, untouched):
  bootstrap-static.json   players, teams, gameweeks, chips
  fixtures.json           all 380 fixtures with FDR
  entry.json              manager summary (bank, value, rank)
  entry-history.json      per-gameweek history + chips used
  entry-transfers.json    every transfer made this season
  entry-picks.json        squad picked for the latest gameweek (absent pre-season)
  prev-season.csv         last season's per-player totals (Vaastav mirror), used as
                          the prior for per-90 rates while this season's sample is small
  element-history.json    per-fixture minutes and starts this season for every player who
                          has featured or is flagged, from element-summary (one call each)
  lineups.json            predicted lineups and injury tags from Rotowire, matched to FPL ids
                          (see lineups.py; best effort)
  top-picks.json          ownership and captaincy among a sample of highly ranked managers
                          (see topmanagers.py; best effort)
"""
import argparse
import concurrent.futures
import os.path
import sys as _sys
_sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://fantasy.premierleague.com/api"
UA = "Mozilla/5.0 (fpl-picker; +https://github.com/alancullinan/fpl-picker)"
DEFAULT_ENTRY = 4853364
VAASTAV = "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"


def get(path, retries=4):
    url = f"{API}/{path}/"
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
        time.sleep(delay)
        delay *= 2


def fetch_prev_season(bootstrap, out):
    """Best effort: last season's players_raw.csv from the Vaastav mirror."""
    try:
        year = int(bootstrap["events"][0]["deadline_time"][:4]) - 1
        url = f"{VAASTAV}/{year}-{str(year + 1)[2:]}/players_raw.csv"
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=30) as r:
            body = r.read()
        path = os.path.join(out, "prev-season.csv")
        with open(path, "wb") as f:
            f.write(body)
        print(f"wrote {path} ({len(body)//1024} KB)")
    except Exception as e:  # noqa: BLE001 - a missing prior must not fail the run
        print(f"prev-season prior unavailable: {e}", file=sys.stderr)


def fetch_element_history(bootstrap, out, workers=8):
    """Per-fixture history for the minutes model, from element-summary.

    Only players with minutes or an availability flag are fetched: unused
    squad players contribute nothing to the estimate. Failures leave a player
    without history, and the model falls back to season totals for them.
    """
    wanted = [e["id"] for e in bootstrap["elements"]
              if (e.get("minutes") or 0) > 0 or e.get("status") != "a" or e.get("news")]
    hist = {}

    def one(pid):
        try:
            d = get(f"element-summary/{pid}", retries=2)
        except Exception:  # noqa: BLE001
            return pid, None
        if not d:
            return pid, None
        rows = sorted(d.get("history", []), key=lambda h: (h.get("round", 0), h.get("kickoff_time") or ""))
        return pid, [[h.get("round"), h.get("minutes", 0), 1 if h.get("starts") else 0] for h in rows]

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for pid, rows in ex.map(one, wanted):
            if rows is not None:
                hist[str(pid)] = rows
    save(out, "element-history.json", hist)
    print(f"element history for {len(hist)} of {len(wanted)} players")


def save(out, name, payload):
    path = os.path.join(out, name)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {path} ({os.path.getsize(path)//1024} KB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", type=int, default=int(os.environ.get("FPL_ENTRY_ID") or DEFAULT_ENTRY))
    ap.add_argument("--out", default="data/raw")
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    bootstrap = get("bootstrap-static")
    if not bootstrap or "elements" not in bootstrap:
        print("bootstrap-static returned no players; is the season live?", file=sys.stderr)
        sys.exit(1)
    save(args.out, "bootstrap-static.json", bootstrap)
    save(args.out, "fixtures.json", get("fixtures"))
    fetch_prev_season(bootstrap, args.out)
    fetch_element_history(bootstrap, args.out)
    try:
        import lineups  # noqa: PLC0415 - sibling module, kept optional
        body = lineups.fetch(os.path.join(args.out, "lineups.html"))
        lineups.write(lineups.match_players(lineups.parse(body), bootstrap), args.out)
    except Exception as e:  # noqa: BLE001 - lineups are optional
        print(f"lineups unavailable: {e}", file=sys.stderr)
    try:
        import topmanagers  # noqa: PLC0415 - sibling module, kept optional
        sys.argv = ["topmanagers", "--out", args.out]
        topmanagers.main()
    except Exception as e:  # noqa: BLE001 - the sample is optional
        print(f"top-manager sample unavailable: {e}", file=sys.stderr)

    entry = get(f"entry/{args.entry}")
    if entry is None:
        print(f"entry {args.entry} not found (private or wrong id); skipping team data", file=sys.stderr)
        return
    save(args.out, "entry.json", entry)
    history = get(f"entry/{args.entry}/history")
    if history is not None and not history.get("current") and any(e.get("finished") for e in bootstrap.get("events", [])):
        time.sleep(3)  # the endpoint sometimes returns no rows mid-season; try once more
        history = get(f"entry/{args.entry}/history") or history
    save(args.out, "entry-history.json", history)
    save(args.out, "entry-transfers.json", get(f"entry/{args.entry}/transfers"))

    # Picks exist only for gameweeks whose deadline has passed. Prefer the
    # current gameweek; before GW1 there is nothing to fetch.
    events = bootstrap.get("events", [])
    current = next((e["id"] for e in events if e.get("is_current")), None)
    if current is None:
        finished = [e["id"] for e in events if e.get("finished")]
        current = max(finished) if finished else None
    if current is not None:
        picks = get(f"entry/{args.entry}/event/{current}/picks")
        if picks:
            picks["_event"] = current
            save(args.out, "entry-picks.json", picks)


if __name__ == "__main__":
    main()
