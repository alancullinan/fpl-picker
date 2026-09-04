# FPL Picker

A small static site that pulls together the numbers needed to pick a Fantasy Premier League
team each week: your current squad, an expected-points estimate for every player, a fixture
ticker, and a planning mode for trying transfers, lineups, captains and chips before the
deadline. No server: a GitHub Action refreshes `data/fpl.json` on request and GitHub Pages
serves the page.

## How it works

```
pipeline/fetch.py   ->  data/raw/*.json      raw FPL API responses (not committed)
pipeline/build.py   ->  data/fpl.json        one bundle the site reads (committed by the Action)
index.html + app.js ->  the site
```

The expected-points model lives in `build.py` and is deliberately simple and inspectable.
Every input it uses is in the bundle, so a number on the site can be traced back by hand.
The rules it encodes are written up in `.claude/skills/fpl/SKILL.md`.

## Setup

1. **Pages**: Settings, Pages, "Deploy from a branch", branch `main`, folder `/ (root)`.
2. **Entry ID**: the pipeline defaults to entry `4853364`. To change it, add a repository
   variable `FPL_ENTRY_ID` (Settings, Secrets and variables, Actions, Variables).
3. **Refreshing data**: the workflow runs on request, not on a schedule. Either press
   "Run workflow" on the Actions tab, or use the site's "Refresh data" button, which asks once
   for a fine-grained personal access token (this repo only, Actions read and write) and keeps
   it in the browser. Each run commits `data/fpl.json` only when something changed.

## Planning mode

The public FPL API only publishes a squad after the gameweek deadline, so changes you make
in the FPL app for the upcoming gameweek are invisible until then. Planning mode fills the
gap: from the confirmed squad, tap players to transfer them out, bring players in from the
Players tab, swap starters and bench, set captain and vice, and pick a chip. The plan is kept
in the browser, checks budget, the three-per-club limit, formation and free transfers, and is
cleared once FPL confirms a newer squad. It is a scratchpad; the transfers still have to be
made in the FPL app.

## Local

```
python3 pipeline/fetch.py          # needs access to fantasy.premierleague.com
python3 pipeline/build.py
python3 -m http.server 8000        # open http://localhost:8000
```

Both scripts use only the Python standard library.

## Data sources

- Official FPL API (`fantasy.premierleague.com/api`): players, prices, xG/xA, fixtures, your entry.
- [Vaastav's FPL dataset](https://github.com/vaastav/Fantasy-Premier-League): last season's
  per-player totals, used as a prior while the new season's sample is small.
