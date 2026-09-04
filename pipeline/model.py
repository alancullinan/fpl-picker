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
    "league_xgc": 1.35,       # goals conceded per game, league average
    "prior_games": 5,         # weight of that prior vs a team's observed xGC
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
    "min_decay": 0.75,        # weight of each older fixture relative to the next newer one
    "min_prior_w": 2.0,       # prior start rate counts as this many fixtures (backtested: 1-3 close, 2 best XI)
    "p60_sub": 0.05,          # chance a substitute appearance reaches 60 minutes
    "mins_sub": 20.0,         # default minutes for a substitute appearance
    "mins_start": 85.0,       # default minutes for a start
    "p60_start": 0.9,         # default chance a start reaches 60 minutes
    "unseen_start": 0.35,     # prior start rate with no history at all
}


def make_prior(pos, price, median_price, prev, P=PARAMS):
    """Per-90 prior rates: last season's if the sample is big enough, else positional × price."""
    scale = price / median_price if median_price else 1.0
    pmin = float(prev.get("minutes") or 0) if prev else 0.0
    if prev and pmin >= P["prev_min_minutes"]:
        g = pmin / 90.0
        return {
            "xg": float(prev.get("expected_goals") or 0) / g, "xa": float(prev.get("expected_assists") or 0) / g,
            "dc": float(prev.get("defensive_contribution") or 0) / g, "saves": float(prev.get("saves") or 0) / g,
            "bonus": float(prev.get("bonus") or 0) / g, "src": "prev",
            "start": min(1.0, float(prev.get("starts") or 0) / 38.0),
        }
    return {
        "xg": PRIOR_XG[pos] * scale ** 1.5, "xa": PRIOR_XA[pos] * scale ** 1.2,
        "dc": PRIOR_DC[pos], "saves": PRIOR_SAVES[pos], "bonus": min(1.0, 0.15 * scale ** 2), "src": "price",
        "start": P["unseen_start"],
    }


def shrink(obs_total, obs_minutes, prior_rate, P=PARAMS):
    pm = P["prior_minutes"]
    return (obs_total + prior_rate * pm / 90.0) / ((obs_minutes + pm) / 90.0)


def rates(state, prior, P=PARAMS):
    m = state["mins"]
    return {
        "xg90": shrink(state["xg"], m, prior["xg"], P), "xa90": shrink(state["xa"], m, prior["xa"], P),
        "dc90": shrink(state["dc"], m, prior["dc"], P), "sv90": shrink(state["saves"], m, prior["saves"], P),
        "bon": shrink(state["bonus"], m, prior["bonus"], P), "src": prior["src"],
    }


def minutes_probs(state, P=PARAMS):
    """(p_play, p_60, expected minutes / 90) for the next fixture.

    With a fixture history (state["recent"], most recent first, as (minutes,
    started) pairs) the estimate is recency-weighted and separates starts from
    substitute appearances. Without one it falls back to season totals.
    """
    chance = state.get("chance", 1.0)
    if state.get("status") in ("u", "n"):
        chance = 0.0
    recent = state.get("recent")
    if P["minutes_model"] >= 1 and recent:
        return _recent_minutes(recent, state.get("prior_start", P["unseen_start"]), chance, P)
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


def _recent_minutes(recent, prior_start, chance, P):
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
    p_play = chance * (p_start + p_sub)
    p_60 = chance * (p_start * p60_start + p_sub * P["p60_sub"])
    frac = chance * (p_start * m_start + p_sub * m_sub) / 90.0
    return p_play, p_60, frac


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
    att = (1.0 + (3 - fx["fdr"]) * P["att_fdr"]) * (1 + P["home_att"] if fx["home"] else 1 - P["home_att"])
    dfn = (1.0 + (fx["fdr"] - 3) * P["con_fdr"]) * (1 - P["home_con"] if fx["home"] else 1 + P["home_con"])
    lam = lam_team * dfn
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


def player_xp(state, prior, fixtures_by_gw, lam_team, P=PARAMS):
    """Return (per-gameweek totals, component breakdown of the first gameweek, rates, p_play).

    fixtures_by_gw: list over upcoming gameweeks, each a list of {fdr, home}
    (empty for a blank, two for a double).
    """
    r = rates(state, prior, P)
    if "prior_start" not in state:
        state = dict(state, prior_start=prior.get("start", P["unseen_start"]))
    p_play, p_60, frac = minutes_probs(state, P)
    totals, parts1 = [], {}
    for i, fxs in enumerate(fixtures_by_gw):
        total = 0.0
        for fx in fxs:
            parts = fixture_xp(state["pos"], r, p_play, p_60, lam_team, fx, P, frac)
            total += sum(parts.values())
            if i == 0:
                for k, v in parts.items():
                    parts1[k] = parts1.get(k, 0.0) + v
        totals.append(total)
    return totals, parts1, r, p_play
