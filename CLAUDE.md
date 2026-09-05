# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

FPL Picker: a static site plus a scheduled data pipeline that helps pick a Fantasy Premier
League team each gameweek. It is a separate project from MatchTrackerPWA, though it follows
the same principles: vanilla JS, no build step, no runtime dependencies, deployed by GitHub
Pages from the `main` branch root.

## Layout

- `pipeline/fetch.py`: raw FPL API responses into `data/raw/` (gitignored). Standard library only.
  Includes one `element-summary` call per featured player (threaded, best effort); a player
  without history falls back to season totals in the minutes model.
- `pipeline/lineups.py`: Rotowire predicted lineups and injury tags matched to FPL ids. HTML
  scraping, best effort; when Rotowire's markup changes, touch the debug-lineups workflow to
  capture a fresh page and develop against it with `--html`.
- `pipeline/topmanagers.py`: samples squads from the top of the overall league for ownership
  and effective ownership. Optional; a failure leaves the site without those columns.
- `pipeline/model.py`: the expected-points model as pure functions of "what was known at the
  deadline", with every tunable in `PARAMS`. Shared by build and backtest.
- `pipeline/build.py`: raw into `data/fpl.json`, the single bundle the site reads, plus a
  prediction snapshot in `data/history/gwNN.json`. Standard library only.
- `worker/`: Cloudflare Worker proxying the Ask box to Claude, so the API key stays off the
  public site. Verifies a Firebase ID token against `ALLOWED_UIDS` and fixes the model and
  token ceiling server-side. It also dispatches the data workflow with a GitHub token of its
  own, so no credential need reach the browser; the page keeps a pasted-token fallback for when
  no Worker is configured. Deployed separately from Pages; `window.FPL_WORKER` in
  `firebase-config.js` switches it on.
- `pipeline/news.py`: reads team news with Claude + web search into structured per-player
  signals, archived per gameweek in `data/news/`. Runs between the two `build.py` calls in the
  workflow, because it needs the bundle and the bundle needs its output. Never an xP input
  until scored - see the skill.
- `pipeline/briefing.py`: asks Claude for the week's advice from the built bundle plus the FPL
  skill, and writes `data/briefing.json`. Advice only - nothing it produces feeds expected
  points. `--dry-run` prints the prompt without calling the API. Needs the ANTHROPIC_API_KEY
  repository secret; without it the workflow step is skipped. Costs about $0.30 a run.
  **The paid steps (news.py and briefing.py) run only on a workflow_dispatch carrying
  `inputs.ai`.** No schedule and no push may ever trigger them - the user pays per call and
  asked for it that way. If you add another API-calling step, put it behind the same input.
- `pipeline/sweep.py`: runs every model parameter over a grid through the backtest and prints
  three-season deltas against the current default. Run it before claiming a coefficient is right.
- `pipeline/backtest.py`: replays past seasons from the Vaastav mirror and scores the model
  against baselines; `--all` scores three seasons and reports the mean, `--set key=value` tries
  parameter changes. Writes `data/backtest.json`.
- `index.html`, `app.js`, `styles.css`: the site. Three views: My Team, Players, Fixtures.
- `.github/workflows/update-data.yml`: runs fetch and build daily at 03:37 UTC, every three
  hours when the next deadline (read from the committed bundle) is within 36 hours, on request,
  and on a push touching the pipeline. Commits the bundle and the prediction snapshot.
- `.claude/skills/fpl/SKILL.md`: the FPL rules (squad, transfers, chips, scoring) and the weekly
  workflow. Read it before touching the model or giving any team advice.

## Rules of the road

- Keep the DATA pipeline (fetch, build, model, backtest) dependency-free, so a refresh always
  runs on a bare runner. `briefing.py` is the one exception: it needs `anthropic`, is installed
  and run as a separate optional step, and must never be able to block a data refresh.
- Never commit `data/raw/`. Commit `data/fpl.json` only via the Action (or deliberately, to seed).
- The FPL API is unofficial and undocumented. Handle missing keys defensively; `fetch.py` treats
  a missing entry as non-fatal so the player data still refreshes.
- A player's history row is evidence only once its own fixture has finished (`_row_played` in
  `build.py`). FPL writes a zero-minute row at the deadline and then counts minutes up live
  during the match, so an unguarded row says a player was dropped before kick-off and says he
  was hooked at half time while he is still on the pitch. `min_decay` weights the newest fixture
  hardest, so either reading moves projections a long way: a live 37-minute row took Haaland's
  p_60 from 0.98 to 0.45. Test at the fixture level, never the gameweek, so Saturday's results
  count on Saturday rather than waiting for the round to be marked finished.
- The model in `model.py` must stay explainable: every term is one line, every input is in the
  bundle. Any change to it, coefficients included, is justified by `backtest.py --all`: the
  MEAN across seasons must improve, and the commit message quotes before and after. Do not ship
  on a single season's result - several apparent gains reversed on another season.
- Bump the `?v=` query on `app.js` and `styles.css` in `index.html` when changing them; GitHub
  Pages caches aggressively.
- Team advice must respect the rules in the skill: squad shape, 3 per club, budget, hits, chips.
- The plan (`localStorage` key `fplplan`) always exists once data loads: it starts as a copy of
  `me.picks` and the UI shows the diff against it. It is dropped when `me.picks_gw` moves on. The public API never shows
  pre-deadline changes, so do not try to "fix" that in the pipeline.
- The "Refresh data" button dispatches the workflow through the GitHub API with a token the
  user pastes once (`localStorage` key `fplpicker.gh`, mirrored to Firestore when signed in).
  Never commit a token anywhere.
- Sync is optional and must stay that way: with `window.FPL_FIREBASE` null the site loads no
  Firebase code and works exactly as before. The Firebase config in `firebase-config.js` is a
  public identifier, but `firestore.rules` is the security boundary - keep it restricted to
  `users/{uid}` and publish any change in the Firebase console, since the file in the repo is
  documentation, not deployment.

## Local run

```
python3 pipeline/fetch.py && python3 pipeline/build.py && python3 -m http.server 8000
```

In sandboxes that cannot reach `fantasy.premierleague.com`, the Vaastav mirror on
raw.githubusercontent.com carries the same fields as CSV and can be converted for testing.
