#!/usr/bin/env python3
"""Sample the picks of highly ranked managers, for ownership among the field.

Overall ownership counts every casual entry; what changes a decision is what
the managers you are competing with hold. This samples the top of the overall
league (id 314) and counts ownership and captaincy among them.

Writes data/raw/top-picks.json:
  {"gw": 3, "sampled": 300, "ranks": [1, 10000],
   "counts": {"<player id>": [owned, captained], ...}}

Best effort: any failure leaves no file and the site simply shows nothing.
Standard library only.
"""
import argparse
import concurrent.futures
import json
import os
import sys
import time
import urllib.error
import urllib.request

API = "https://fantasy.premierleague.com/api"
UA = "Mozilla/5.0 (fpl-picker; +https://github.com/alancullinan/fpl-picker)"
OVERALL_LEAGUE = 314
PAGE_SIZE = 50


def get(path, retries=3):
    url = f"{API}/{path}"
    delay = 2
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (404, 403):
                return None
            if attempt == retries - 1:
                raise
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            if attempt == retries - 1:
                raise
        time.sleep(delay)
        delay *= 2
    return None


def sample_entries(pages, workers=6):
    """Entry ids from the given standings pages of the overall league."""
    def one(page):
        d = get(f"leagues-classic/{OVERALL_LEAGUE}/standings/?page_standings={page}")
        if not d:
            return []
        return [(r["entry"], r["rank"]) for r in d.get("standings", {}).get("results", [])]

    out = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for rows in ex.map(one, pages):
            out.extend(rows)
    return out


def fetch_picks(entries, gw, workers=8):
    """(owned, captained) counts per player id across the sampled entries."""
    counts = {}
    ok = 0

    def one(entry_id):
        try:
            return get(f"entry/{entry_id}/event/{gw}/picks/")
        except Exception:  # noqa: BLE001 - one bad entry must not stop the sample
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as ex:
        for d in ex.map(one, [e for e, _ in entries]):
            if not d or not d.get("picks"):
                continue
            ok += 1
            for pk in d["picks"]:
                pid = str(pk["element"])
                c = counts.setdefault(pid, [0, 0])
                c[0] += 1
                if pk.get("is_captain"):
                    c[1] += 1
    return counts, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="data/raw")
    ap.add_argument("--managers", type=int, default=300, help="approximate sample size")
    ap.add_argument("--depth", type=int, default=10000, help="sample spread over this many ranks")
    ap.add_argument("--gw", type=int, default=0, help="gameweek (default: latest finished)")
    a = ap.parse_args()

    gw = a.gw
    if not gw:
        bs_path = os.path.join(a.out, "bootstrap-static.json")
        if not os.path.exists(bs_path):
            print("no bootstrap-static.json; run fetch.py first", file=sys.stderr)
            return
        with open(bs_path, encoding="utf-8") as f:
            events = json.load(f).get("events", [])
        finished = [e["id"] for e in events if e.get("finished")]
        if not finished:
            print("no finished gameweek yet; skipping top-manager sample", file=sys.stderr)
            return
        gw = max(finished)

    # Spread the sample through the top `depth` ranks rather than taking only
    # the leaders, whose position is partly luck.
    n_pages = max(1, a.managers // PAGE_SIZE)
    last_page = max(1, a.depth // PAGE_SIZE)
    step = max(1, last_page // n_pages)
    pages = list(range(1, last_page + 1, step))[:n_pages]

    entries = sample_entries(pages)
    if not entries:
        print("could not read the overall league standings; skipping", file=sys.stderr)
        return
    counts, ok = fetch_picks(entries, gw)
    if ok < 20:
        print(f"only {ok} squads read; too few to be meaningful, skipping", file=sys.stderr)
        return
    ranks = [r for _, r in entries]
    payload = {"gw": gw, "sampled": ok, "ranks": [min(ranks), max(ranks)], "counts": counts}
    path = os.path.join(a.out, "top-picks.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, separators=(",", ":"))
    print(f"wrote {path}: {ok} squads from ranks {min(ranks)}-{max(ranks)}, GW{gw}, {len(counts)} players")


if __name__ == "__main__":
    main()
