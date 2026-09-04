"""The expected-points model, shared by build.py (live) and backtest.py (replay).

Everything is a plain function of "what was known at the deadline" so the same
code can be scored against past seasons. PARAMS holds every tunable number;
backtest.py can evaluate alternative parameter sets side by side.
"""
import math

POS = {1: "GKP", 2: "DEF", 3: "MID", 4: "FWD"}
GOAL_PTS = {1: 10, 2: 6, 3: 5, 4: 4}
CS_PTS = {1: 4, 2: 4, 3: 1, 4: 0}
ASSIST_PTS = 3
DEFCON_THRESHOLD = {2: 10, 3: 12, 4: 12}

# Positional priors per 90 at the position's median price. Attacking rates
# scale with price because price is FPL's own prior on output.
PRIOR_XG = {1: 0.0, 2: 0.06, 3: 0.16, 4: 0.35}
PRIOR_XA = {1: 0.0, 2: 0.06, 3: 0.14, 4: 0.10}
PRIOR_DC = {1: 0.0, 2: 8.5, 3: 6.0, 4: 3.0}
PRIOR_SAVES = {1: 2.7, 2: 0.0, 3: 0.0, 4: 0.0}

PARAMS = {
    "prior_minutes": 900,     # this season's per-90 rates are shrunk towards a prior worth ten games
    "prev_min_minutes": 450,  # last season counts as a prior only above this
    "prev_conf": 0.0,         # scale the prior's weight by how many minutes it rests on:
                              # weight x pmin/(pmin + prev_conf). Tested at 300-1500 and left
                              # OFF: no variant moved the three-season mean beyond noise.
                              # Thin-sample players are not systematically wrong, only
                              # uncertain, so the site marks confidence instead (see "ev").
    "league_xgc": 1.35,       # goals conceded per game, league average
    "prior_games": 5,         # weight of that prior vs a team's observed xGC
    # Opponent strength. FDR is FPL's five-level rating, set before the season.
    # strength_model 1 uses attack/defence ratings fitted to match results.
    # Left OFF: ratings lose to FDR on all three backtested seasons however
    # they are shrunk (best XI 56.5-58.0 against 58.2), and even a 15% blend
    # is worse. FDR is a forward-looking judgement of squad quality; ratings
    # take most of a season to learn what FDR knows in August, and goal counts
    # are noisy. The team's own defensive level already comes from its
    # goalkeepers' xGC, which is the low-noise half of this term.
    "strength_model": 0,
    "str_blend": 1.0,         # 0 = pure FDR, 1 = pure ratings, between = geometric blend
    "lam_direct": 0.0,        # 0: expected goals conceded = own xGC x opponent multiplier;
                              # 1: taken directly from the fixture (fx["lam"]), the shape a
                              # bookmaker's goals market would provide. Also tested and worse
                              # (best XI 57.8 against 58.2), which is why bookmaker odds were
                              # not pursued: this term has little headroom to win back.
    "str_decay": 0.985,       # weight per day of age on a past result
    "str_prior": 6.0,         # matches of "average team" mixed into each rating
    "str_iters": 6,           # passes of the attack/defence fit
    "str_clip": 0.55,         # ratings are clipped to 1 ± this, so one thrashing cannot dominate
    "att_fdr": 0.12,          # attack multiplier per FDR step away from 3
    "con_fdr": 0.15,          # concede multiplier per FDR step away from 3
    "home_att": 0.05,         # home/away attack swing
    "home_con": 0.08,         # home/away concede swing
    "bonus_shrink": 0.7,      # bonus per game is noisy; damp it
    "defcon_scale": 3.0,      # logistic width around the DefCon threshold
    "start_w": 0.85,          # weight of start rate vs minutes share in p_play
    "play_floor": 0.15,       # p_play floor for anyone with minutes this season
    "unseen_play": 0.15,      # p_play for a fit player with no minutes yet
    "p60_cap": 0.95,
    # Recent-minutes model (used when a player's fixture history is available)
    "minutes_model": 1,       # 1 = recency-weighted fixture history, 0 = season totals
    "min_decay": 0.55,        # weight of each older fixture relative to the next newer one
                              # (0.55 beats 0.75 on rank correlation and RMSE in all three
                              # backtested seasons; recent games say most about selection)
    "min_prior_w": 0.5,       # prior start rate counts as this many fixtures (backtested: best rank corr, full and early season)
    "p60_sub": 0.05,          # chance a substitute appearance reaches 60 minutes
    "mins_sub": 20.0,         # default minutes for a substitute appearance
    "mins_start": 85.0,       # default minutes for a start
    "p60_start": 0.9,         # default chance a start reaches 60 minutes
    "unseen_start": 0.35,     # prior start rate with no history at all
    "prev_shrink": 0.0,       # minutes of positional prior blended into last season's rates (0 = off;
                              # tested at 450-1800, improved 2025/26 only and hurt the mean)
    # Set-piece duty. A penalty is worth about 0.79 xG and a team wins roughly
    # 0.13 a game, so first choice on penalties is about 0.10 xG per 90. This
    # adjusts the PRIOR, not the observed rate: a player's own xG already
    # contains the penalties he has taken, so the uplift fades as data arrives.
    # Tested at 0.05-0.25 and left OFF: a gain on 2025/26 reversed on 2024/25,
    # so it was noise. Set-piece duty is shown in the site as information
    # instead. Re-test with --all before turning either on.
    "pen_xg": 0.0,            # extra xG per 90 in the prior for the first-choice taker
    "pen_xg_second": 0.0,     # same for second choice
    "sp_xa": 0.0,             # extra xA per 90 in the prior for the first-choice corner/free-kick taker
    # Predicted lineups (Rotowire): how far to move the start probability
    "lineup_w": 0.7,          # weight of a predicted lineup vs the minutes history
    "lineup_w_confirmed": 0.95,
    "lineup_out": 0.25,       # availability cap for a player Rotowire tags OUT
    "lineup_ques": 0.75,      # availability cap for a player Rotowire tags QUES
    "tag_fade": 0.5,          # how much of an OUT tag's effect remains each further gameweek
}


def _order(v):
    """Set-piece order as an int; the API uses null for 'not on them'."""
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return 0


def make_prior(pos, price, median_price, prev, P=PARAMS, pen_order=None, sp_order=None):
    """Per-90 prior rates: last season's if the sample is big enough, else positional × price.

    pen_order / sp_order are the player's CURRENT set-piece ranks (1 = first
    choice). They lift the prior, so a newly appointed taker is not judged
    solely on a history without penalties.
    """
    scale = price / median_price if median_price else 1.0
    pmin = float(prev.get("minutes") or 0) if prev else 0.0
    if prev and pmin >= P["prev_min_minutes"]:
        g = pmin / 90.0
        k = P["prev_shrink"] / 90.0  # positional prior worth this many games
        pos_xg, pos_xa = PRIOR_XG[pos] * scale ** 1.5, PRIOR_XA[pos] * scale ** 1.2
        pen = _pen_uplift(pen_order, P)
        sp = P["sp_xa"] if _order(sp_order) == 1 else 0.0
        return {
            "xg": (float(prev.get("expected_goals") or 0) + (pos_xg + pen) * k) / (g + k) + pen,
            "xa": (float(prev.get("expected_assists") or 0) + (pos_xa + sp) * k) / (g + k) + sp,
            "dc": (float(prev.get("defensive_contribution") or 0) + PRIOR_DC[pos] * k) / (g + k),
            "saves": (float(prev.get("saves") or 0) + PRIOR_SAVES[pos] * k) / (g + k),
            "bonus": (float(prev.get("bonus") or 0) + min(1.0, 0.15 * scale ** 2) * k) / (g + k), "src": "prev",
            "start": min(1.0, float(prev.get("starts") or 0) / 38.0),
            "minutes": pmin,
        }
    return {
        "xg": PRIOR_XG[pos] * scale ** 1.5 + _pen_uplift(pen_order, P),
        "xa": PRIOR_XA[pos] * scale ** 1.2 + (P["sp_xa"] if _order(sp_order) == 1 else 0.0),
        "dc": PRIOR_DC[pos], "saves": PRIOR_SAVES[pos], "bonus": min(1.0, 0.15 * scale ** 2), "src": "price",
        "start": P["unseen_start"], "minutes": None,
    }


def _pen_uplift(pen_order, P):
    o = _order(pen_order)
    return P["pen_xg"] if o == 1 else P["pen_xg_second"] if o == 2 else 0.0


def shrink(obs_total, obs_minutes, prior_rate, P=PARAMS, prior_minutes=None):
    """Blend an observed total with a prior rate.

    prior_minutes is how many minutes the prior itself rests on; when
    prev_conf is set, a prior from a short season carries less weight.
    """
    pm = P["prior_minutes"]
    if P["prev_conf"] > 0 and prior_minutes is not None:
        pm *= prior_minutes / (prior_minutes + P["prev_conf"])
    return (obs_total + prior_rate * pm / 90.0) / ((obs_minutes + pm) / 90.0)


def rates(state, prior, P=PARAMS):
    m = state["mins"]
    pm = prior.get("minutes")
    return {
        "xg90": shrink(state["xg"], m, prior["xg"], P, pm), "xa90": shrink(state["xa"], m, prior["xa"], P, pm),
        "dc90": shrink(state["dc"], m, prior["dc"], P, pm), "sv90": shrink(state["saves"], m, prior["saves"], P, pm),
        "bon": shrink(state["bonus"], m, prior["bonus"], P, pm), "src": prior["src"],
    }


def minutes_probs(state, P=PARAMS, ahead=0):
    """(p_play, p_60, expected minutes / 90) for a fixture `ahead` gameweeks away.

    With a fixture history (state["recent"], most recent first, as (minutes,
    started) pairs) the estimate is recency-weighted and separates starts from
    substitute appearances. Without one it falls back to season totals.

    A predicted lineup speaks only about the next fixture (ahead == 0). An
    OUT tag fades by tag_fade per further gameweek; a QUES tag is next-fixture
    only. FPL's own availability flag is applied throughout.
    """
    chance = state.get("chance", 1.0)
    if state.get("status") in ("u", "n"):
        chance = 0.0
    tag = state.get("inj_tag")
    if tag in ("OUT", "SUS", "INJ"):
        cap = 1.0 - (1.0 - P["lineup_out"]) * (P["tag_fade"] ** ahead)
        chance = min(chance, cap)
    elif tag in ("QUES", "DOUB", "GTD") and ahead == 0:
        chance = min(chance, P["lineup_ques"])
    if state.get("oracle_minutes") is not None:   # backtest diagnostic only
        m = state["oracle_minutes"]
        return (1.0 if m > 0 else 0.0), (1.0 if m >= 60 else 0.0), m / 90.0
    recent = state.get("recent")
    if P["minutes_model"] >= 1 and recent:
        lineup = state.get("lineup") if ahead == 0 else None
        return _recent_minutes(recent, state.get("prior_start", P["unseen_start"]), chance, P, lineup, state.get("lineup_confirmed", False))
    mins, games = state["mins"], max(state["team_games"], 1)
    if mins > 0:
        start_rate = min(1.0, state["starts"] / games)
        share = min(1.0, mins / (games * 90.0))
        p_play = chance * max(P["play_floor"], P["start_w"] * start_rate + (1 - P["start_w"]) * share)
        p_60 = chance * share * P["p60_cap"]
    else:
        p_play = chance * P["unseen_play"]
        p_60 = p_play * 0.6
    return p_play, p_60, p_play


def _recent_minutes(recent, prior_start, chance, P, lineup=None, confirmed=False):
    d, wsum, w_start, w_sub = P["min_decay"], 0.0, 0.0, 0.0
    sm = s60 = sw = 0.0   # weighted minutes, 60+ flags and weight over starts
    bm = bw = 0.0         # weighted minutes and weight over sub appearances
    for k, (m, started) in enumerate(recent):
        w = d ** k
        wsum += w
        if started:
            w_start += w; sw += w; sm += w * m; s60 += w * (1.0 if m >= 60 else 0.0)
        elif m > 0:
            w_sub += w; bw += w; bm += w * m
    pw = P["min_prior_w"]
    p_start = (w_start + pw * prior_start) / (wsum + pw)
    p_sub = w_sub / (wsum + pw)
    m_start = sm / sw if sw else P["mins_start"]
    p60_start = s60 / sw if sw else P["p60_start"]
    m_sub = bm / bw if bw else P["mins_sub"]
    # A predicted lineup pulls the start probability towards 1 (named) or 0
    # (team has a lineup, player not in it); substitutes come from the rest.
    if lineup in ("xi", "bench"):
        w = P["lineup_w_confirmed"] if confirmed else P["lineup_w"]
        if lineup == "xi":
            p_start = p_start + (1.0 - p_start) * w
            p_sub = p_sub * (1.0 - w)
        else:
            p_start = p_start * (1.0 - w)
            p_sub = min(1.0 - p_start, p_sub + (1.0 - p_sub) * w * 0.3)
    p_play = chance * (p_start + p_sub)
    p_60 = chance * (p_start * p60_start + p_sub * P["p60_sub"])
    frac = chance * (p_start * m_start + p_sub * m_sub) / 90.0
    return p_play, p_60, frac


def team_ratings(matches, now=None, P=PARAMS):
    """Attack and defence ratings per team from played matches. 1.0 is average.

    matches: [{"home": tid, "away": tid, "hg": int, "ag": int, "age_days": float}]
    Attack above 1 scores more than average; defence above 1 concedes more.
    Both are shrunk towards 1 by str_prior matches of average, so a team with
    two games played sits close to average rather than at an extreme.
    """
    teams = {m["home"] for m in matches} | {m["away"] for m in matches}
    att = {t: 1.0 for t in teams}
    dfn = {t: 1.0 for t in teams}
    if not matches:
        return att, dfn
    wts = [P["str_decay"] ** max(0.0, m.get("age_days", 0.0)) for m in matches]
    total_goals = sum((m["hg"] + m["ag"]) * w for m, w in zip(matches, wts))
    total_w = sum(2 * w for w in wts)
    avg = (total_goals / total_w) if total_w else 1.4
    if avg <= 0:
        return att, dfn
    prior = P["str_prior"]
    for _ in range(int(P["str_iters"])):
        sa = {t: prior for t in teams}
        na = {t: prior for t in teams}   # attack: goals scored / expected
        sd = {t: prior for t in teams}
        nd = {t: prior for t in teams}   # defence: goals conceded / expected
        for m, w in zip(matches, wts):
            h, a = m["home"], m["away"]
            sa[h] += m["hg"] * w / max(0.2, dfn[a] * avg); na[h] += w
            sa[a] += m["ag"] * w / max(0.2, dfn[h] * avg); na[a] += w
            sd[h] += m["ag"] * w / max(0.2, att[a] * avg); nd[h] += w
            sd[a] += m["hg"] * w / max(0.2, att[h] * avg); nd[a] += w
        lo, hi = 1 - P["str_clip"], 1 + P["str_clip"]
        att = {t: min(hi, max(lo, sa[t] / na[t])) for t in teams}
        dfn = {t: min(hi, max(lo, sd[t] / nd[t])) for t in teams}
    return att, dfn


def team_xgc(gk_states, P=PARAMS):
    """A team's expected goals conceded per game, shrunk towards the league prior."""
    obs = sum(g["xgc"] for g in gk_states)
    games = sum(g["mins"] for g in gk_states) / 90.0
    return (obs + P["league_xgc"] * P["prior_games"]) / (games + P["prior_games"])


def fixture_xp(pos, r, p_play, p_60, lam_team, fx, P=PARAMS, frac=None):
    """Points expected from one fixture, broken into components.

    frac is expected minutes as a fraction of 90; per-90 rates scale by it.
    """
    if frac is None:
        frac = p_play
    home_a = 1 + P["home_att"] if fx["home"] else 1 - P["home_att"]
    home_c = 1 - P["home_con"] if fx["home"] else 1 + P["home_con"]
    fdr_att = 1.0 + (3 - fx["fdr"]) * P["att_fdr"]
    fdr_con = 1.0 + (fx["fdr"] - 3) * P["con_fdr"]
    if P["strength_model"] >= 1 and fx.get("odef") is not None:
        # Scoring chances scale with how leaky the opponent is; goals conceded
        # scale with how dangerous they are. Blended with FDR, which is a
        # forward-looking judgement of squad quality that results-based
        # ratings take most of a season to learn.
        b = P["str_blend"]
        att = (fx["odef"] ** b) * (fdr_att ** (1 - b)) * home_a
        dfn = (fx["oatt"] ** b) * (fdr_con ** (1 - b)) * home_c
    else:
        att = fdr_att * home_a
        dfn = fdr_con * home_c
    lam = fx["lam"] if (P["lam_direct"] >= 1 and fx.get("lam") is not None) else lam_team * dfn
    p_cs = math.exp(-lam)
    appearance = 2.0 * p_60 + 1.0 * (p_play - p_60)
    attack = (r["xg90"] * GOAL_PTS[pos] + r["xa90"] * ASSIST_PTS) * att * frac
    cs = CS_PTS[pos] * p_cs * p_60
    conceded = -(lam / 2.0) * p_60 if pos in (1, 2) else 0.0
    defcon = 0.0
    if pos in DEFCON_THRESHOLD and r["dc90"] > 0:
        z = (r["dc90"] - DEFCON_THRESHOLD[pos]) / P["defcon_scale"]
        defcon = 2.0 * (1.0 / (1.0 + math.exp(-z))) * p_60
    saves = (r["sv90"] / 3.0) * frac * dfn if pos == 1 else 0.0
    bonus = r["bon"] * P["bonus_shrink"] * att * frac
    return {"app": appearance, "att": attack, "cs": cs + conceded, "dc": defcon, "sv": saves, "bon": bonus}


def player_xp(state, prior, fixtures_by_gw, lam_team, P=PARAMS, full=False):
    """Return (per-gameweek totals, component breakdown of the first gameweek, rates, p_play).

    fixtures_by_gw: list over upcoming gameweeks, each a list of {fdr, home}
    (empty for a blank, two for a double). With full=True the player is assumed
    to play every minute of every fixture: the "if he plays" view.
    """
    r = rates(state, prior, P)
    if "prior_start" not in state:
        state = dict(state, prior_start=prior.get("start", P["unseen_start"]))
    p_play, p_60, frac = (1.0, 1.0, 1.0) if full else minutes_probs(state, P)
    totals, parts1 = [], {}
    for i, fxs in enumerate(fixtures_by_gw):
        pp, p6, fr = (p_play, p_60, frac) if (i == 0 or full) else minutes_probs(state, P, ahead=i)
        total = 0.0
        for fx in fxs:
            parts = fixture_xp(state["pos"], r, pp, p6, lam_team, fx, P, fr)
            total += sum(parts.values())
            if i == 0:
                for k, v in parts.items():
                    parts1[k] = parts1.get(k, 0.0) + v
        totals.append(total)
    return totals, parts1, r, p_play
