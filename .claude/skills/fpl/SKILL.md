---
name: fpl
description: Fantasy Premier League rules, 2026/27 scoring, chips and the weekly team-picking workflow for this repo. Use whenever giving FPL transfer, captaincy, lineup or chip advice, or when changing the expected-points model in pipeline/build.py.
---

# FPL Picker skill

Everything here is a hard constraint on advice. A suggestion that breaks a rule is wrong,
however good the numbers look.

## Squad rules

- 15 players: 2 GKP, 5 DEF, 5 MID, 3 FWD. Budget £100.0m at the start; afterwards team value plus bank.
- Max 3 players from one club, counting the whole 15.
- Starting XI: 1 GKP, 3 to 5 DEF, 2 to 5 MID, 1 to 3 FWD, 11 in total. Bench order matters:
  auto-subs come in bench order if a starter plays 0 minutes, keeping the formation valid.
- Captain scores double, vice-captain takes over if the captain plays 0 minutes.
- Selling price: you get your purchase price plus half of any rise, rounded down to £0.1m.
  The public API does not expose purchase prices, so the site assumes selling at current price.
  Say so when a swap is close to the budget line.

## Transfers

- 1 free transfer (FT) per gameweek. Unused FTs bank up to a maximum of 5.
- Each transfer beyond your FTs costs 4 points ("a hit"), deducted from that gameweek.
- After a hit you drop to 1 FT for the next gameweek. Otherwise next FT = min(5, FT - used + 1).
- Wildcard and Free Hit gameweeks: transfers are free, and you keep the FTs you had, still
  gaining the usual +1 (capped at 5) for the following gameweek.
- Unlimited free transfers before the GW1 deadline. The GW2 count starts at 1.
- `build.py` `free_transfers()` replays the season with these rules; treat its answer as an
  estimate and check against the FPL app if a hit is on the line.

## Chips (2026/27)

Two full sets of four chips. Set one must be used by the GW19 deadline (2 January 2027) and
does not carry over. Set two is GW20 to GW38.

| Chip | Effect | Notes |
|---|---|---|
| Wildcard | Unlimited free transfers that gameweek, changes permanent | Cannot be cancelled once confirmed |
| Free Hit | Unlimited transfers for one gameweek, squad reverts after | Good for blank or double gameweeks |
| Bench Boost | All 15 players score | Best in a double gameweek with a full playing bench |
| Triple Captain | Captain scores triple | Best on a premium with a double gameweek |

Only one chip per gameweek. The bundle's `chips` array carries each chip's window and
`me.chips_used` what has been played.

## Scoring (2026/27)

| Action | GKP | DEF | MID | FWD |
|---|---|---|---|---|
| Playing up to 60 min | 1 | 1 | 1 | 1 |
| Playing 60+ min | 2 | 2 | 2 | 2 |
| Goal | 10 | 6 | 5 | 4 |
| Assist | 3 | 3 | 3 | 3 |
| Clean sheet (60+ min) | 4 | 4 | 1 | 0 |
| Every 2 goals conceded | -1 | -1 | 0 | 0 |
| Every 3 saves | 1 | | | |
| Penalty save | 5 | | | |
| Penalty miss | -2 | -2 | -2 | -2 |
| Yellow / red card | -1 / -3 | -1 / -3 | -1 / -3 | -1 / -3 |
| Own goal | -2 | -2 | -2 | -2 |
| Defensive contribution | | 2 | 2 | 2 |
| Bonus | 1 to 3 to the top BPS players in each match | | | |

Defensive contribution (DefCon): 2 points, once per match, when a DEF reaches 10 clearances,
blocks, interceptions and tackles (CBIT), or a MID or FWD reaches 12 with ball recoveries also
counting (CBIRT). Unchanged from 2025/26.

Bonus Points System changes for 2026/27, from the Premier League's announcement: projected
bonus is now shown live after 20 minutes of each match; the -1 BPS for being tackled is gone;
goalkeepers get 2 BPS per save, +1 for a save inside the box and +1 for saving a "big chance"
(so a penalty save is 7 BPS); the "save from outside the box" BPS metric is removed. These are
BPS (bonus) changes only. The fantasy points for saves stay at 1 per 3 saves. Verify against the
official rules page if a scoring question hinges on it; the rules page was not reachable when
this was written.

## Data this repo produces

`data/fpl.json`, built by `pipeline/build.py`:

- `next_gw`, `deadline`, `current_gw`, `events[]`, `chips[]`.
- `teams{}`: `fixtures` is an array over the next 8 gameweeks; each entry is a list (empty for a
  blank, two entries for a double) of `{opp_short, home, fdr, kickoff}`.
- `players[]`: FPL fields (`price`, `sel`, `status`, `chance`, `news`, `form`, `pts`, `min`, xG, xA,
  DefCon and saves per 90) plus the model: `p_play`, `xp1`, `xp5`, `xp_gw[8]`, `parts` (next GW
  breakdown) and `rates` (the shrunk per-90 rates and whether the prior came from last season
  or from price).
- `me`: `picks[]` with slot, captain flags and multiplier; `bank`, `value`, `free_transfers`,
  `chips_used[]`, `history[]`, `transfers[]`.

## The model, in one paragraph

Per fixture: appearance points times minutes probability, plus (xG per 90 × goal points +
xA per 90 × 3) scaled by an attacking fixture multiplier, plus clean-sheet points × P(0 goals
conceded) from a Poisson on the team's xG conceded scaled by a defensive multiplier, minus
expected goals-conceded penalties for GKP/DEF, plus 2 × P(DefCon) from a logistic on the
player's DefCon per 90 against the threshold, plus saves/3 for keepers, plus shrunk bonus per
game. Per-90 rates are shrunk towards a prior (last season's rate if 450+ minutes, otherwise a
positional rate scaled by price) with the prior worth 900 minutes. Fixture multipliers:
attack 1 + (3 - FDR) × 0.12, concede 1 + (FDR - 3) × 0.15, home ±5% attack and ±8% concede.
Doubles add, blanks give 0.

Minutes: each player's recent fixtures (most recent weighted 1, each older one ×0.75) give a
start probability, a substitute-appearance probability, typical minutes for each, and the
chance of reaching 60. A prior start rate from last season counts as two fixtures. Per-90
rates scale by expected minutes, so a habitual 20-minute substitute is valued as such.
FPL's availability flag multiplies everything. The bundle exposes `p_play`, `p_60`, `xmin`
(expected minutes) and `recent` (last six fixtures' minutes) per player.

Known weaknesses: no penalty-taker bonus, no predicted-lineup input, bonus is crude, and
FPL's FDR is a blunt instrument. Improve these before adding anything else.

## Changing the model

The model is `pipeline/model.py`; `pipeline/backtest.py` is the gate. Run it before and after
any change (`--set key=value` tries a parameter without editing code) and keep a change only
if rank correlation and best-XI points improve over 2025/26. History: season-totals minutes
gave rank correlation 0.48 and best-XI 52.7 points per gameweek; the recent-fixtures minutes
model gives 0.57 and 56.5. Points-per-game form scores 0.38 and 40.8. Expert or crowd signals
go through the same gate before they touch xP.

## What the API can and cannot show

Picks and transfers for a gameweek appear in the public API only after that gameweek's
deadline. Before the deadline `me.picks` is the previous confirmed squad and `me.transfers`
omits pending moves. The site's planning mode holds pre-deadline intentions in the browser;
it is not in the bundle, so ask the user what they have planned rather than assuming the
confirmed squad is current.

## Weekly workflow

1. Confirm the data is fresh (`generated` in the bundle, or the footer of the site). The
   workflow runs on request only: run "Update FPL data" from the Actions tab or the site's
   Refresh button after the press conferences, typically Friday morning.
2. Read `me`: bank, FTs, chips left in the current half, current XI and captain.
3. Injuries and doubts in the 15: anyone with `status` not `a` or `chance` below 75 is a
   candidate to move. Quote the `news` text.
4. Best XI and captain from `xp1`, respecting formation rules. Captain the highest `xp1`;
   mention the second choice when they are within about 0.5 points.
5. Transfers: rank one-for-one swaps by `xp5` gain within budget and the 3-per-club limit. Only
   recommend a hit when the gain over the next 5 gameweeks clearly exceeds 4 points, and say so.
   Prefer banking an FT to a marginal move.
6. Chips: flag a chip only with a concrete trigger (double gameweek, blank, five or more
   problems in the squad for a Wildcard). Remind about the GW19 expiry for the first set as it
   approaches.
7. Report: three sections, "Do this", "Consider", "Ignore". Every recommendation carries the
   number that justifies it and the rule it satisfies.
