# FPL Picker

A small static site that pulls together the numbers needed to pick a Fantasy Premier League
team each week: your current squad, an expected-points estimate for every player, and a
fixture ticker. No server: a GitHub Action refreshes `data/fpl.json` a few times a day and
GitHub Pages serves the page.

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
3. **First run**: Actions, "Update FPL data", "Run workflow". After that it runs on its
   own schedule. Each run commits `data/fpl.json` only when something changed.

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
