# -*- coding: utf-8 -*-
"""Feature transformer and random-forest pipelines for next-season UFA MVP prediction.

Two questions, one feature set:

  build_score_pipeline()   RandomForestRegressor -> next season's MVP score
  build_chance_pipeline()  CalibratedChance      -> P(top-5 in MVP score next season)

Both are random forests. The chance model is the same forest with a logistic
calibration on top, because a classifier trained directly on the top-5 label loses to
a do-nothing baseline - see CalibratedChance for why.

MVP score is the additive, season-total version of the weighting fit in fit_mvp.py:
a running point total made of the counting stats already in the data, with the weights
chosen so the real 2025 MVP finishes first (train_mvp.py refits and saves them to
mvp_weights.json; DEFAULT_WEIGHTS below is what that search returned).

Team success is deliberately NOT part of the score - a score built from a player's own
production stays a player statistic. Team record enters as a *feature* instead, so the
forest can learn how much it moves next season's number.

Everything a season row needs is in ufa_player_season_2021_2026.csv (fetch_seasons.py).
"""
import numpy as np
import pandas as pd
from sklearn.base import BaseEstimator, ClassifierMixin, TransformerMixin
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline

__all__ = ["MVPFeatureTransformer", "CalibratedChance", "DEFAULT_WEIGHTS",
           "MVP_COMPONENTS", "mvp_score", "make_year_pairs", "build_score_pipeline",
           "build_chance_pipeline", "chance_targets", "FEATURE_TOTALS", "TOP_N",
           "SCORE_FOREST", "CHANCE_FOREST"]

TOP_N = 5                       # "MVP candidate" = this many places in next season's score

# Season totals carried into the model untouched.
FEATURE_TOTALS = [
    "goals", "assists", "hockey_assists", "blocks", "callahans", "callahans_thrown",
    "completions", "throw_attempts", "throwaways", "stalls", "drops", "catches",
    "yards_thrown", "yards_received", "hucks_completed", "hucks_attempted",
    "o_points_played", "o_points_scored", "d_points_played", "d_points_scored",
    "pulls", "ob_pulls", "seconds_played", "o_opportunities", "o_opportunity_scores",
    "d_opportunities", "d_opportunity_stops",
    "team_wins", "team_losses", "team_ties", "team_standing",
]

# MVP-score components, each a function of one season-total row.
MVP_COMPONENTS = {
    "scores":        lambda d: d["goals"] + d["assists"] + d["hockey_assists"],
    "rec_yards_100": lambda d: d["yards_received"] / 100.0,
    "thr_yards_100": lambda d: d["yards_thrown"] / 100.0,
    "blocks":        lambda d: d["blocks"],
    "turnovers":     lambda d: d["throwaways"] + d["stalls"] + d["drops"],
    "completions_100": lambda d: d["completions"] / 100.0,
}

# Fit by train_mvp.py: of every weighting on the grid that ranks the 2025 MVP first,
# the one closest to the neutral baseline (1, 1, 1, 1, -1, 1).
DEFAULT_WEIGHTS = {
    "scores": 1.0,
    "rec_yards_100": 1.0,
    "thr_yards_100": 1.0,
    "blocks": 1.0,
    "turnovers": -1.0,
    "completions_100": 1.0,
}


def mvp_score(df, weights=None):
    """Additive MVP score for each row of a season-totals frame -> pd.Series."""
    w = DEFAULT_WEIGHTS if weights is None else weights
    total = pd.Series(0.0, index=df.index)
    for name, component in MVP_COMPONENTS.items():
        total = total + float(w.get(name, 0.0)) * component(df).astype(float)
    return total


def _safe_ratio(num, den, floor=1.0):
    """num/den with the denominator floored, so a 3-point cameo cannot post a huge rate."""
    return num.astype(float) / np.maximum(den.astype(float), float(floor))


class MVPFeatureTransformer(BaseEstimator, TransformerMixin):
    """Turn raw UFA season totals into the feature matrix the forests are trained on.

    Input is a DataFrame of season totals (one row per player-season, the columns
    fetch_seasons.py writes). Output is an all-numeric DataFrame: the raw totals, plus
    volume and production derivations, optional per-point rates and efficiency ratios,
    and optionally the MVP score itself. Identifier and target columns are dropped, so
    the same frame can be handed to fit and to predict without leaking the answer.

    Parameters
    ----------
    per_point : bool
        Add per-point-played rates (scores, yards, blocks, turnovers, plus/minus).
        Points played is the denominator rather than games because the season-totals
        endpoint reports no game count.
    include_rates : bool
        Add efficiency ratios: completion %, huck %, O-line conversion, D-line stop
        rate, share of points on offence, team win %.
    include_mvp_score : bool
        Add this season's MVP score as a feature. It is the strongest single predictor
        of next season's, so leave it on unless you are measuring the others.
    weights : dict or None
        MVP-score component weights. None uses DEFAULT_WEIGHTS.
    min_points : int
        Floor for rate denominators, in points played.

    Learns nothing from the data - fit only records the column layout.
    """

    def __init__(self, per_point=True, include_rates=True, include_mvp_score=True,
                 weights=None, min_points=10):
        self.per_point = per_point
        self.include_rates = include_rates
        self.include_mvp_score = include_mvp_score
        self.weights = weights
        self.min_points = min_points

    # ---------------------------------------------------------------- internals
    def _build(self, X):
        d = pd.DataFrame(X).copy()
        missing = [c for c in FEATURE_TOTALS if c not in d.columns]
        if missing:
            raise KeyError("season-total columns missing from input: %s" % ", ".join(missing))
        d[FEATURE_TOTALS] = d[FEATURE_TOTALS].apply(pd.to_numeric, errors="coerce").fillna(0.0)

        f = d[FEATURE_TOTALS].astype(float).copy()

        points = f["o_points_played"] + f["d_points_played"]
        turnovers = f["throwaways"] + f["stalls"] + f["drops"]
        scores = f["goals"] + f["assists"]
        involved = scores + f["hockey_assists"]
        yards = f["yards_thrown"] + f["yards_received"]
        games = f["team_wins"] + f["team_losses"] + f["team_ties"]

        f["points_played"] = points
        f["minutes_played"] = f["seconds_played"] / 60.0
        f["turnovers"] = turnovers
        f["total_scores"] = scores
        f["scores_involved"] = involved
        f["total_yards"] = yards
        f["plus_minus"] = scores + f["blocks"] - turnovers

        if self.per_point:
            floor = self.min_points
            f["scores_per_point"] = _safe_ratio(involved, points, floor)
            f["yards_per_point"] = _safe_ratio(yards, points, floor)
            f["blocks_per_point"] = _safe_ratio(f["blocks"], points, floor)
            f["turnovers_per_point"] = _safe_ratio(turnovers, points, floor)
            f["plus_minus_per_point"] = _safe_ratio(f["plus_minus"], points, floor)
            f["yards_per_minute"] = _safe_ratio(yards, f["minutes_played"], floor)

        if self.include_rates:
            f["completion_pct"] = _safe_ratio(f["completions"], f["throw_attempts"], 1.0)
            f["huck_pct"] = _safe_ratio(f["hucks_completed"], f["hucks_attempted"], 1.0)
            f["huck_rate"] = _safe_ratio(f["hucks_attempted"], f["throw_attempts"], 1.0)
            f["o_conversion"] = _safe_ratio(f["o_opportunity_scores"], f["o_opportunities"], 1.0)
            f["d_stop_rate"] = _safe_ratio(f["d_opportunity_stops"], f["d_opportunities"], 1.0)
            f["o_point_share"] = _safe_ratio(f["o_points_played"], points, self.min_points)
            f["team_win_pct"] = _safe_ratio(f["team_wins"], games, 1.0)

        if self.include_mvp_score:
            f["mvp_score"] = mvp_score(d, self.weights)

        return f.replace([np.inf, -np.inf], 0.0).fillna(0.0).astype(float)

    # ---------------------------------------------------------------- sklearn API
    def fit(self, X, y=None):
        """Record the column layout. Nothing is learned from the values."""
        built = self._build(X)
        self.feature_names_in_ = np.asarray(list(pd.DataFrame(X).columns), dtype=object)
        self.feature_names_out_ = np.asarray(list(built.columns), dtype=object)
        self.n_features_out_ = built.shape[1]
        return self

    def transform(self, X):
        built = self._build(X)
        if hasattr(self, "feature_names_out_"):
            built = built[list(self.feature_names_out_)]
        return built

    def get_feature_names_out(self, input_features=None):
        return np.asarray(self.feature_names_out_, dtype=object)


def make_year_pairs(seasons, weights=None, top_n=TOP_N, min_points=0):
    """Join each player-season to the same player's next season.

    Returns one row per (player, year) that has a year+1 season, carrying this season's
    stats plus two targets:

      mvp_score_next   next season's MVP score                        (regression)
      is_top5_next     next season's score in the league top `top_n`  (classification)

    Also keeps mvp_score and mvp_rank for the current season, for reporting.
    """
    d = pd.DataFrame(seasons).copy()
    d["mvp_score"] = mvp_score(d, weights)
    d["mvp_rank"] = d.groupby("year")["mvp_score"].rank(ascending=False, method="min")

    nxt = d[["player_id", "year", "mvp_score", "mvp_rank", "team_wins"]].copy()
    nxt.columns = ["player_id", "year", "mvp_score_next", "mvp_rank_next", "team_wins_next"]
    nxt["year"] = nxt["year"] - 1                      # line season N+1 up with season N

    pairs = d.merge(nxt, on=["player_id", "year"], how="inner")
    pairs["is_top5_next"] = (pairs["mvp_rank_next"] <= top_n).astype(int)
    if min_points:
        played = pairs["o_points_played"] + pairs["d_points_played"]
        pairs = pairs[played >= min_points]
    return pairs.reset_index(drop=True)


# Forest settings, chosen by leave-one-season-out CV over the training years only,
# averaged across five seeds. Heavy leaf smoothing: a top-5 season is roughly 1 row in
# 130, and deeper trees just memorise which players were good last year.
SCORE_FOREST = dict(n_estimators=500, min_samples_leaf=10, max_features="sqrt")
CHANCE_FOREST = dict(SCORE_FOREST)     # the chance model is the same forest, calibrated


def _forest_kwargs(defaults, random_state, overrides):
    kw = dict(defaults, n_jobs=-1, random_state=random_state)
    kw.update(overrides)
    return kw


def chance_targets(pairs):
    """The two-column y that CalibratedChance.fit expects: (score, top-5 label)."""
    return np.column_stack([pairs["mvp_score_next"].to_numpy(float),
                            pairs["is_top5_next"].to_numpy(float)])


class CalibratedChance(BaseEstimator, ClassifierMixin):
    """Chance of a top-5 MVP-score season next year, via the regressor that works.

    A RandomForestClassifier trained directly on the top-5 label does WORSE than
    assuming every player repeats himself - there are only 21 top-5 seasons in the
    whole dataset, which is not enough to learn a decision boundary from 50 features.
    The signal that does exist is in the continuous score, where every row is a
    training example.

    So this predicts the score with a random forest, then learns a one-parameter
    logistic map from predicted score to P(top-5). The forest sees 2,806 informative
    rows instead of 21, and the calibrator has a single coefficient to fit, which the
    21 positives can support. The result beats the baseline on both ROC-AUC and
    average precision, and - unlike a rank - its output is an actual probability.

    fit(X, y) takes y as TWO columns, next season's score and the top-5 label, because
    it trains two stages on the same rows. Use chance_targets(pairs) to build it.

    The calibrator is fitted on out-of-fold forest predictions, so it never sees the
    forest's own training values - fitted predictions are far too optimistic and would
    produce probabilities that are confident and wrong.
    """

    def __init__(self, n_estimators=500, min_samples_leaf=10, max_features="sqrt",
                 n_splits=5, random_state=0, n_jobs=-1):
        self.n_estimators = n_estimators
        self.min_samples_leaf = min_samples_leaf
        self.max_features = max_features
        self.n_splits = n_splits
        self.random_state = random_state
        self.n_jobs = n_jobs

    def _forest(self):
        return RandomForestRegressor(
            n_estimators=self.n_estimators, min_samples_leaf=self.min_samples_leaf,
            max_features=self.max_features, random_state=self.random_state,
            n_jobs=self.n_jobs)

    def fit(self, X, y):
        y = np.asarray(y, dtype=float)
        if y.ndim != 2 or y.shape[1] != 2:
            raise ValueError(
                "CalibratedChance needs a two-column y - next season's score and the "
                "top-5 label. Build it with chance_targets(pairs); got shape %r."
                % (y.shape,))
        score, label = y[:, 0], y[:, 1].astype(int)
        Xd = X if hasattr(X, "iloc") else pd.DataFrame(X)

        oof = np.empty(len(Xd))
        splitter = KFold(n_splits=self.n_splits, shuffle=True,
                         random_state=self.random_state)
        for train_idx, test_idx in splitter.split(Xd):
            fold = self._forest().fit(Xd.iloc[train_idx], score[train_idx])
            oof[test_idx] = fold.predict(Xd.iloc[test_idx])

        self.calibrator_ = LogisticRegression(max_iter=1000).fit(
            oof.reshape(-1, 1), label)
        self.regressor_ = self._forest().fit(Xd, score)
        self.classes_ = np.array([0, 1])
        self.n_positives_ = int(label.sum())
        return self

    def predicted_score(self, X):
        """The underlying next-season score prediction, before calibration."""
        return self.regressor_.predict(X)

    def predict_proba(self, X):
        s = self.regressor_.predict(X).reshape(-1, 1)
        return self.calibrator_.predict_proba(s)

    def predict(self, X):
        return (self.predict_proba(X)[:, 1] >= 0.5).astype(int)


def build_score_pipeline(weights=None, random_state=0, **forest):
    """Pipeline predicting next season's MVP score (a number).

    Keyword arguments are passed through to RandomForestRegressor, overriding
    SCORE_FOREST.
    """
    return Pipeline([
        ("features", MVPFeatureTransformer(weights=weights)),
        ("model", RandomForestRegressor(**_forest_kwargs(SCORE_FOREST, random_state, forest))),
    ])


def build_chance_pipeline(weights=None, random_state=0, **forest):
    """Pipeline predicting the chance of a top-5 MVP-score season next year.

    Fit it with the two-column target: pipe.fit(pairs, chance_targets(pairs)).
    Read it with predict_proba(X)[:, 1] - the positive class is well under 1% of rows,
    so the hard predict() label is almost always 0 and tells you nothing. Keyword
    arguments are passed through to CalibratedChance, overriding CHANCE_FOREST.
    """
    return Pipeline([
        ("features", MVPFeatureTransformer(weights=weights)),
        ("model", CalibratedChance(**_forest_kwargs(CHANCE_FOREST, random_state, forest))),
    ])
