# FPL Picker

A small static site that pulls together the numbers needed to pick a Fantasy Premier League
team each week: your current squad, an expected-points estimate for every player, a fixture
ticker, and a planning mode for trying transfers, lineups, captains and chips before the
deadline. No server: a GitHub Action refreshes `data/fpl.json` daily and more often near the
deadline, and GitHub Pages serves the page.

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

`pipeline/backtest.py` replays finished seasons one deadline at a time, rebuilding what the
model would have known and scoring its next-gameweek prediction against actual points. Two
baselines are scored the same way: season points per game, and the xP column of the Vaastav
dataset. Every change to the model must improve the mean across all three seasons before it
ships; single-season gains have repeatedly turned out to be noise.

```
python3 pipeline/backtest.py --all --fetch          # first time
python3 pipeline/backtest.py --all                  # the gate
python3 pipeline/backtest.py --all --set att_fdr=0.2
python3 pipeline/backtest.py --all --out data/backtest.json
```

`pipeline/sweep.py` runs every model parameter over a grid and prints the three-season change
against the current default, so a coefficient set by judgement can be checked rather than
trusted.

`--oracle minutes` feeds in actual minutes as a diagnostic: it shows that most remaining error
is minutes prediction (best XI 58.2 with predicted minutes, 64.3 with real ones), which is
where model work pays.

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
3. **Refreshing data**: the workflow runs every morning (03:37 UTC, early and off the hour
   because GitHub queues scheduled runs and can start them hours late), and every three hours
   once the next deadline is within 36 hours so predicted lineups and press-conference news
   arrive in time. For anything in between, press "Run workflow" on the Actions tab or use the
   site's "Refresh data" button, which asks once for a fine-grained personal access token (this
   repo only, Actions read and write) and keeps it in the browser. Each run commits
   `data/fpl.json` only when something changed. GitHub pauses schedules on repositories with
   no commits for 60 days, so over the summer a manual run restarts them.

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

## The weekly briefing (optional)

`pipeline/briefing.py` hands the built bundle and the FPL rules skill to Claude and asks for
the judgement the numbers cannot make: whether a small expected gain is worth a differential
risk, which suggestions rest on too little evidence, whether to bank the transfer. It writes
`data/briefing.json`, which the site shows at the top of My Team.

It runs as a separate workflow step and needs an `ANTHROPIC_API_KEY` repository secret
(Settings, Secrets and variables, Actions). Without the secret the step is skipped and the
site simply shows no briefing.

A run costs about $0.30, most of it thinking tokens, so it is rate limited two ways: the
workflow does not call it on pushes (development should not spend money), and the script skips
if a briefing for the same gameweek is under ten hours old. In practice that is one a day,
plus one more as a deadline approaches. Budget roughly $2 a week in season.

The briefing is advice, never an input: nothing it says feeds expected points, for the same
reason ownership does not. `--dry-run` prints the prompt without calling the API.

## Team news (optional)

`pipeline/news.py` asks Claude, with web search, to read team news the numeric model cannot
see - a manager resting someone, a midweek European tie, a return from injury, a change of
role - and returns one structured signal per player. It runs only within 96 hours of the
deadline. Signals are archived per gameweek in `data/news/`, shown in the site, and are
**not** an input to expected points until they have been scored against actual minutes.

## Cross-device sync (optional)

`firebase-config.js` holds a Firebase web config. With it set, the site offers Google sign-in
in the footer and keeps the plan, the table settings and the GitHub refresh token in Firestore
under `users/{uid}`, so every browser signed in with the same Google account shares them.
Local storage stays as the offline copy and the newer timestamp wins. Set the config to `null`
to turn sync off; nothing loads and the site behaves as before.

The config values are public identifiers, not secrets. Access is controlled by
`firestore.rules`, which allows a signed-in user to read and write only their own subtree.
Those rules must be published in the Firebase console (Firestore, Rules) for the project to be
safe, and the site's domain must be listed under Authentication, Settings, Authorized domains.

## Data sources

- Official FPL API (`fantasy.premierleague.com/api`): players, prices, xG/xA, fixtures, your entry,
  and each featured player's per-fixture minutes (one `element-summary` call per player, fetched
  concurrently) for the minutes model.
- [Vaastav's FPL dataset](https://github.com/vaastav/Fantasy-Premier-League): last season's
  per-player totals, used as a prior while the new season's sample is small.
- The FPL API's overall league standings, sampled through the top 10,000, for ownership and
  captaincy among strong managers (`pipeline/topmanagers.py`).
- [Rotowire predicted lineups](https://www.rotowire.com/soccer/lineups.php): predicted XIs and
  injury tags for the upcoming matches, matched to FPL players by name within each club.
  Scraped from HTML, so treated as optional; `pipeline/lineups.py --html page.html` parses a
  saved page, and the "Debug lineups page" workflow captures one to the `debug-lineups` branch.
