# -*- coding: utf-8 -*-
"""Tests for pipeline_def.py.  Run:  python test_pipeline_def.py   (pytest also works)"""
import numpy as np
import pandas as pd
from sklearn.base import clone
from sklearn.pipeline import Pipeline

from pipeline_def import (MVPFeatureTransformer, CalibratedChance, DEFAULT_WEIGHTS,
                          mvp_score, make_year_pairs, build_score_pipeline,
                          build_chance_pipeline, chance_targets)

RAW = ["goals", "assists", "hockey_assists", "blocks", "callahans", "callahans_thrown",
       "completions", "throw_attempts", "throwaways", "stalls", "drops", "catches",
       "yards_thrown", "yards_received", "hucks_completed", "hucks_attempted",
       "o_points_played", "o_points_scored", "d_points_played", "d_points_scored",
       "pulls", "ob_pulls", "seconds_played", "o_opportunities", "o_opportunity_scores",
       "d_opportunities", "d_opportunity_stops", "team_wins", "team_losses",
       "team_ties", "team_standing"]


def frame(n=40, seed=0, years=(2021, 2022)):
    """A small synthetic season panel with the same columns as the real CSV."""
    rng = np.random.default_rng(seed)
    out = []
    for y in years:
        d = pd.DataFrame({c: rng.integers(0, 60, n).astype(float) for c in RAW})
        d["player_id"] = ["p%02d" % i for i in range(n)]
        d["year"] = y
        d["o_points_played"] = rng.integers(20, 200, n).astype(float)
        d["d_points_played"] = rng.integers(0, 120, n).astype(float)
        d["seconds_played"] = rng.integers(500, 20000, n).astype(float)
        out.append(d)
    return pd.concat(out, ignore_index=True)


def test_init_only_assigns():
    """__init__ must assign its arguments unchanged - nothing computed, nothing renamed."""
    w = dict(DEFAULT_WEIGHTS)
    t = MVPFeatureTransformer(per_point=False, include_rates=False,
                              include_mvp_score=False, weights=w, min_points=7)
    assert t.per_point is False
    assert t.include_rates is False
    assert t.include_mvp_score is False
    assert t.weights is w          # the same object, not a copy or a normalised version
    assert t.min_points == 7
    # sklearn reads those attributes straight back out of get_params
    assert t.get_params() == {"per_point": False, "include_rates": False,
                              "include_mvp_score": False, "weights": w, "min_points": 7}
    assert clone(t).get_params() == t.get_params()


def test_defaults_are_not_mutated():
    a, b = MVPFeatureTransformer(), MVPFeatureTransformer()
    a.fit(frame())
    assert b.get_params() == MVPFeatureTransformer().get_params()


def test_fit_returns_self():
    t = MVPFeatureTransformer()
    assert t.fit(frame()) is t
    assert t.fit(frame(), np.zeros(80)) is t      # y is accepted and ignored


def test_transform_is_numeric_and_finite():
    t = MVPFeatureTransformer()
    X = t.fit_transform(frame())
    assert len(X) == 80
    assert list(X.columns) == list(t.get_feature_names_out())
    assert X.select_dtypes(include="number").shape[1] == X.shape[1], "non-numeric column leaked"
    assert np.isfinite(X.to_numpy()).all(), "NaN or inf in features"
    for leak in ("player_id", "year", "mvp_score_next", "is_top5_next"):
        assert leak not in X.columns


def test_zero_denominators_do_not_blow_up():
    d = frame(n=5)
    for c in ("o_points_played", "d_points_played", "throw_attempts", "hucks_attempted",
              "seconds_played", "o_opportunities", "d_opportunities"):
        d[c] = 0.0
    X = MVPFeatureTransformer().fit_transform(d)
    assert np.isfinite(X.to_numpy()).all()


def test_flags_change_the_feature_set():
    d = frame()
    wide = MVPFeatureTransformer().fit(d).get_feature_names_out()
    narrow = MVPFeatureTransformer(include_rates=False, include_mvp_score=False,
                                   per_point=False).fit(d).get_feature_names_out()
    assert "mvp_score" in wide and "mvp_score" not in narrow
    assert any(c.endswith("_per_point") for c in wide)
    assert not any(c.endswith("_per_point") for c in narrow)
    assert set(narrow) < set(wide)


def test_transform_is_row_independent():
    """One row transformed alone must match that row inside a batch (safe for predict)."""
    d = frame(n=6)
    t = MVPFeatureTransformer().fit(d)
    batch = t.transform(d)
    single = t.transform(d.iloc[[3]])
    np.testing.assert_allclose(single.to_numpy()[0], batch.to_numpy()[3])


def test_missing_column_is_reported_clearly():
    d = frame().drop(columns=["blocks"])
    try:
        MVPFeatureTransformer().fit_transform(d)
    except KeyError as e:
        assert "blocks" in str(e)
    else:
        assert False, "missing column should raise"


def test_mvp_score_is_the_weighted_sum():
    d = pd.DataFrame([{c: 0.0 for c in RAW}])
    d.loc[0, ["goals", "assists", "hockey_assists"]] = [10, 5, 2]
    d.loc[0, "yards_received"] = 300.0
    d.loc[0, "blocks"] = 4.0
    d.loc[0, "throwaways"] = 3.0
    w = DEFAULT_WEIGHTS
    want = (w["scores"] * 17 + w["rec_yards_100"] * 3.0 + w["blocks"] * 4
            + w["turnovers"] * 3)
    assert abs(mvp_score(d, w).iloc[0] - want) < 1e-9


def test_make_year_pairs_links_consecutive_seasons():
    d = frame(n=4, years=(2021, 2022, 2023))
    d = d[~((d.player_id == "p00") & (d.year == 2022))]      # p00 misses a season
    pairs = make_year_pairs(d, top_n=2)
    assert set(pairs["year"]) == {2021, 2022}                # 2023 has no following year
    assert ("p00", 2021) not in set(zip(pairs.player_id, pairs.year))
    assert pairs["is_top5_next"].sum() == 2 * pairs["year"].nunique()
    # the target really is next season's score
    row = pairs[(pairs.player_id == "p01") & (pairs.year == 2021)].iloc[0]
    nxt = d[(d.player_id == "p01") & (d.year == 2022)]
    assert abs(row["mvp_score_next"] - mvp_score(nxt, DEFAULT_WEIGHTS).iloc[0]) < 1e-9


def test_pipelines_fit_and_predict():
    pairs = make_year_pairs(frame(n=60, years=(2021, 2022, 2023)))
    reg, clf = build_score_pipeline(random_state=0), build_chance_pipeline(random_state=0)
    assert isinstance(reg, Pipeline) and isinstance(clf, Pipeline)
    assert reg.fit(pairs, pairs["mvp_score_next"]) is reg
    assert len(reg.predict(pairs)) == len(pairs)
    clf.fit(pairs, chance_targets(pairs))
    p = clf.predict_proba(pairs)[:, 1]
    assert p.shape == (len(pairs),) and ((p >= 0) & (p <= 1)).all()


def test_pipeline_is_clonable_and_tunable():
    reg = build_score_pipeline()
    reg.set_params(features__include_rates=False, model__n_estimators=10)
    assert reg.get_params()["features__include_rates"] is False
    assert clone(reg).get_params()["model__n_estimators"] == 10


def test_chance_model_needs_both_targets():
    """Two stages, two targets - a 1-D y must fail loudly, not silently mis-train."""
    pairs = make_year_pairs(frame(n=40, years=(2021, 2022, 2023)))
    clf = build_chance_pipeline(random_state=0, n_estimators=20, n_splits=3)
    try:
        clf.fit(pairs, pairs["is_top5_next"])
    except ValueError as e:
        assert "two-column" in str(e) and "chance_targets" in str(e)
    else:
        assert False, "a 1-D target should raise"
    assert clf.fit(pairs, chance_targets(pairs)) is clf


def test_chance_model_returns_probabilities():
    pairs = make_year_pairs(frame(n=60, years=(2021, 2022, 2023)))
    clf = build_chance_pipeline(random_state=0, n_estimators=20, n_splits=3)
    clf.fit(pairs, chance_targets(pairs))
    p = clf.predict_proba(pairs)
    assert p.shape == (len(pairs), 2)
    np.testing.assert_allclose(p.sum(axis=1), 1.0, atol=1e-9)
    assert ((p >= 0) & (p <= 1)).all()
    model = clf.named_steps["model"]
    assert hasattr(model, "calibrator_") and hasattr(model, "regressor_")
    # the score behind the probability is available, and probability rises with it
    feats = clf.named_steps["features"].transform(pairs)
    s = model.predicted_score(feats)
    assert np.corrcoef(s, p[:, 1])[0, 1] > 0.9


def test_calibrated_chance_init_only_assigns():
    c = CalibratedChance(n_estimators=7, min_samples_leaf=2, max_features=0.5,
                         n_splits=3, random_state=11, n_jobs=1)
    assert c.get_params() == {"n_estimators": 7, "min_samples_leaf": 2,
                              "max_features": 0.5, "n_splits": 3,
                              "random_state": 11, "n_jobs": 1}
    assert clone(c).get_params() == c.get_params()


if __name__ == "__main__":
    fails = 0
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            try:
                f()
                print("PASS", n)
            except Exception as e:
                fails += 1
                print("FAIL", n, "->", type(e).__name__, e)
    raise SystemExit(fails)
