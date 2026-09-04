# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What this is

FPL Picker: a static site plus a scheduled data pipeline that helps pick a Fantasy Premier
League team each gameweek. It is a separate project from MatchTrackerPWA, though it follows
the same principles: vanilla JS, no build step, no runtime dependencies, deployed by GitHub
Pages from the `main` branch root.

## Layout

- `pipeline/fetch.py`: raw FPL API responses into `data/raw/` (gitignored). Standard library only.
- `pipeline/build.py`: raw into `data/fpl.json`, the single bundle the site reads. Contains the
  expected-points model. Standard library only.
- `index.html`, `app.js`, `styles.css`: the site. Three views: My Team, Players, Fixtures.
- `.github/workflows/update-data.yml`: runs fetch and build on a schedule and commits the bundle.
- `.claude/skills/fpl/SKILL.md`: the FPL rules (squad, transfers, chips, scoring) and the weekly
  workflow. Read it before touching the model or giving any team advice.

## Rules of the road

- Keep both pipeline scripts dependency-free; the Action runs on a bare runner.
- Never commit `data/raw/`. Commit `data/fpl.json` only via the Action (or deliberately, to seed).
- The FPL API is unofficial and undocumented. Handle missing keys defensively; `fetch.py` treats
  a missing entry as non-fatal so the player data still refreshes.
- The model in `build.py` must stay explainable: every term is one line, every input is in the
  bundle. If you change a coefficient, say why in the commit message.
- Bump the `?v=` query on `app.js` and `styles.css` in `index.html` when changing them; GitHub
  Pages caches aggressively.
- Team advice must respect the rules in the skill: squad shape, 3 per club, budget, hits, chips.

## Local run

```
python3 pipeline/fetch.py && python3 pipeline/build.py && python3 -m http.server 8000
```

In sandboxes that cannot reach `fantasy.premierleague.com`, the Vaastav mirror on
raw.githubusercontent.com carries the same fields as CSV and can be converted for testing.
