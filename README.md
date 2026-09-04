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

The expected-points model lives in `pipeline/model.py` and is deliberately simple and
inspectable. Every input it uses is in the bundle, so a number on the site can be traced back
by hand. The rules it encodes are written up in `.claude/skills/fpl/SKILL.md`.

## Measuring the model

`pipeline/backtest.py` replays a finished season one deadline at a time, rebuilding what the
model would have known and scoring its next-gameweek prediction against actual points. Two
baselines are scored the same way: season points per game, and the xP column of the Vaastav
dataset. Every change to the model should be justified by this before it ships.

```
python3 pipeline/backtest.py --season 2025-26 --prior 2024-25 --fetch   # first time
python3 pipeline/backtest.py --set att_fdr=0.2                          # try a parameter
python3 pipeline/backtest.py --out data/backtest.json                   # record the result
```

Metrics: rank correlation between predicted and actual points, RMSE, actual points of the
predicted best 1-4-4-2, actual points of the predicted top player, and the mean actual points
of the fifty highest predictions. The replay is blind to injuries and news, which the live
model does see, so absolute numbers understate live accuracy; the comparison is what counts.

The live pipeline also snapshots each run's next-gameweek predictions to `data/history/`, so
the live model, availability flags included, can be scored once results are in.

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
in the FPL app for the upcoming gameweek are invisible until then. The My Team tab therefore
shows one thing: your squad for the upcoming gameweek, starting as a copy of the last
confirmed squad. Tap players to transfer them out, bring players in from the Players tab,
swap starters and bench, set captain and vice, and tap a chip in the Chips card. A "Changes
from confirmed squad" list under the pitch records every difference, and the confirmed
squad itself sits in a collapsible reference below. The plan is kept in the browser, checks
budget, the three-per-club limit, formation and free transfers, and is cleared once FPL
confirms a newer squad. It is a scratchpad; the transfers still have to be made in the FPL app.

## Data sources

- Official FPL API (`fantasy.premierleague.com/api`): players, prices, xG/xA, fixtures, your entry,
  and each featured player's per-fixture minutes (one `element-summary` call per player, fetched
  concurrently) for the minutes model.
- [Vaastav's FPL dataset](https://github.com/vaastav/Fantasy-Premier-League): last season's
  per-player totals, used as a prior while the new season's sample is small.
