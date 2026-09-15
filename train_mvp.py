# -*- coding: utf-8 -*-
"""Fit, evaluate and apply the next-season MVP models defined in pipeline_def.py.

Steps
  1. Anchor the MVP-score weights: of every weighting on the grid that ranks the real
     2025 MVP (Tobe Decraene) first, keep the one closest to the neutral baseline.
     Same method as fit_mvp.py, run on season totals instead of per-game vectors.
  2. Build (season N -> season N+1) pairs from ufa_player_season_2021_2026.csv.
  3. Hold out the most recent pair year as a test set - train on everything before it,
     so the evaluation is a real forecast, never a shuffled split across seasons.
  4. Report against a persistence baseline (next season = this season).
  5. Refit on every pair and forecast the season after the last one in the data.

Outputs mvp_weights.json, pipeline.joblib and mvp_predictions_<year>.csv.
"""
import io
import itertools
import json
import math
import os

import sys

import joblib
import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.metrics import (average_precision_score, mean_absolute_error,
                             r2_score, roc_auc_score)

from pipeline_def import (MVP_COMPONENTS, TOP_N, build_chance_pipeline,
                          build_score_pipeline, chance_targets, make_year_pairs,
                          mvp_score)

SRC = os.path.dirname(os.path.abspath(__file__))
SEASONS = os.path.join(SRC, "ufa_player_season_2021_2026.csv")
MVP_2025 = "tdecraene"          # the real 2025 UFA MVP, per the dashboard this repo builds
SEED = 0

# Plenty of UFA players have accents in their names; the Windows console does not.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


# ---------------------------------------------------------------- 1. score weights
BASELINE = {"scores": 1.0, "rec_yards_100": 1.0, "thr_yards_100": 1.0,
            "blocks": 1.0, "turnovers": -1.0, "completions_100": 1.0}
GRID = {
    "scores":          [1.0],                               # anchors the scale
    "rec_yards_100":   [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0],
    "thr_yards_100":   [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0],
    "blocks":          [0.0, 0.5, 1.0, 1.5, 2.0, 3.0],
    "turnovers":       [-3.0, -2.0, -1.5, -1.0, -0.5, 0.0],
    "completions_100": [0.0, 0.5, 1.0, 1.5, 2.0, 3.0],
}
KEYS = list(MVP_COMPONENTS)


def fit_weights(seasons, year=2025, winner=MVP_2025):
    """Least-distorted weighting on the grid that still ranks the known MVP first."""
    d = seasons[seasons["year"] == year]
    comp = pd.DataFrame({k: fn(d).astype(float) for k, fn in MVP_COMPONENTS.items()})
    comp.index = d["player_id"].to_numpy()
    if winner not in comp.index:
        raise SystemExit("anchor player %s has no %d season" % (winner, year))

    mat = comp.to_numpy()
    won = comp.loc[winner].to_numpy()
    others = comp.index != winner

    best, hits = None, 0
    for combo in itertools.product(*[GRID[k] for k in KEYS]):
        w = np.asarray(combo)
        if (mat[others] @ w >= won @ w).any():
            continue
        hits += 1
        dist = math.sqrt(sum((combo[i] - BASELINE[k]) ** 2 for i, k in enumerate(KEYS)))
        if best is None or dist < best[0]:
            best = (dist, combo)

    if best is None:
        raise SystemExit("no weighting on this grid ranks %s first in %d" % (winner, year))

    dist, combo = best
    weights = dict(zip(KEYS, combo))
    print("MVP-score weights (%d of %d grid weightings rank the %d MVP first;"
          " distance from neutral %.2f)" % (1, hits, year, dist))
    for k in KEYS:
        print("   %-16s %+5.2f   (neutral %+.1f)" % (k, weights[k], BASELINE[k]))
    return weights


# ---------------------------------------------------------------- reporting helpers
def _num(v):
    """Probabilities need decimals, scores do not."""
    return "%8.3f" % v if isinstance(v, float) and abs(v) < 1.5 else "%8.1f" % v


def leaderboard(df, score_col, n=10, extra=()):
    cols = ["first_name", "last_name", "team_abbrev", score_col] + list(extra)
    top = df.nlargest(n, score_col)[cols]
    for i, (_, r) in enumerate(top.iterrows(), 1):
        tail = "".join(" " + _num(r[c]) for c in extra)
        print("   %2d. %-26s %-4s %s%s"
              % (i, (r["first_name"] + " " + r["last_name"])[:26], r["team_abbrev"],
                 _num(r[score_col]), tail))


def cross_validate_by_year(pairs, weights, seeds=(0, 1, 2)):
    """Leave-one-season-out over every pair year, repeated across seeds.

    The single hold-out below is the honest forecast, but it rests on one season with
    five positive labels in it - far too few to rank two models by. This runs every
    season as the test set in turn and reports the spread, which is usually wider than
    the gap between the model and the baseline it is being compared to.
    """
    years = sorted(pairs["year"].unique())
    reg_mae, reg_r2, clf_auc, clf_ap = [], [], [], []
    base_mae, base_r2, base_auc, base_ap = [], [], [], []

    for y in years:
        train, test = pairs[pairs["year"] != y], pairs[pairs["year"] == y]
        truth, y_top = test["mvp_score_next"], test["is_top5_next"]
        naive = test["mvp_score"]
        base_mae.append(mean_absolute_error(truth, naive))
        base_r2.append(r2_score(truth, naive))
        base_auc.append(roc_auc_score(y_top, naive))
        base_ap.append(average_precision_score(y_top, naive))
        for s in seeds:
            p = build_score_pipeline(weights=weights, random_state=s).fit(
                train, train["mvp_score_next"]).predict(test)
            reg_mae.append(mean_absolute_error(truth, p))
            reg_r2.append(r2_score(truth, p))
            q = build_chance_pipeline(weights=weights, random_state=s).fit(
                train, chance_targets(train)).predict_proba(test)[:, 1]
            clf_auc.append(roc_auc_score(y_top, q))
            clf_ap.append(average_precision_score(y_top, q))

    print("\n" + "=" * 74)
    print("LEAVE-ONE-SEASON-OUT: %d seasons x %d seeds, mean +/- sd across folds"
          % (len(years), len(seeds)))
    print("=" * 74)
    ms = lambda v: "%7.3f +/- %.3f" % (np.mean(v), np.std(v))
    print("   %-26s %18s %18s" % ("", "random forest", "persistence"))
    for label, a, b in (("score MAE (lower better)", reg_mae, base_mae),
                        ("score R2", reg_r2, base_r2),
                        ("top-5 ROC-AUC", clf_auc, base_auc),
                        ("top-5 avg precision", clf_ap, base_ap)):
        print("   %-26s %18s %18s" % (label, ms(a), ms(b)))


def evaluate(pairs, test_year, weights):
    """Train on every pair before test_year, forecast test_year, report."""
    train = pairs[pairs["year"] < test_year]
    test = pairs[pairs["year"] == test_year]
    print("\n" + "=" * 74)
    print("HOLD-OUT: train %d->%d pairs (%d rows, years %d-%d) | test %d->%d (%d rows)"
          % (train["year"].min(), train["year"].max() + 1, len(train),
             train["year"].min(), train["year"].max(),
             test_year, test_year + 1, len(test)))
    print("=" * 74)

    # --- next-season MVP score -------------------------------------------------
    reg = build_score_pipeline(weights=weights, random_state=SEED)
    reg.fit(train, train["mvp_score_next"])
    pred = reg.predict(test)
    truth = test["mvp_score_next"].to_numpy()
    naive = test["mvp_score"].to_numpy()          # persistence: same as this season

    print("\nnext-season MVP SCORE (random forest regressor)")
    print("   %-22s %8s %8s %8s" % ("", "MAE", "R2", "Spearman"))
    for label, p in (("random forest", pred), ("persistence baseline", naive)):
        print("   %-22s %8.2f %8.3f %8.3f"
              % (label, mean_absolute_error(truth, p), r2_score(truth, p),
                 spearmanr(truth, p).statistic))

    # --- chance of an MVP-calibre season ---------------------------------------
    clf = build_chance_pipeline(weights=weights, random_state=SEED)
    clf.fit(train, chance_targets(train))
    prob = clf.predict_proba(test)[:, 1]
    y = test["is_top5_next"].to_numpy()

    print("\nchance of a top-%d season (calibrated random forest)"
          " - %d of %d test players actually made it"
          % (TOP_N, int(y.sum()), len(y)))
    print("   %-22s %8s %8s" % ("", "ROC-AUC", "avg prec"))
    for label, p in (("random forest", prob), ("persistence baseline", naive)):
        print("   %-22s %8.3f %8.3f"
              % (label, roc_auc_score(y, p), average_precision_score(y, p)))
    for k in (5, 10, 25):
        caught = y[np.argsort(-prob)[:k]].sum()
        print("   top-%-3d most likely       caught %d of %d" % (k, caught, int(y.sum())))

    # --- what the forest leans on ----------------------------------------------
    names = reg.named_steps["features"].get_feature_names_out()
    imp = pd.Series(reg.named_steps["model"].feature_importances_, index=names)
    print("\ntop 12 features (regressor)")
    for n, v in imp.nlargest(12).items():
        print("   %-24s %.3f" % (n, v))

    # --- the actual test-season leaderboard, predicted vs real ------------------
    out = test.copy()
    out["predicted"] = pred
    out["chance"] = prob
    print("\npredicted top 10 for %d (actual score and actual rank alongside)" % (test_year + 1))
    leaderboard(out, "predicted", 10, extra=("chance", "mvp_score_next", "mvp_rank_next"))
    print("\nwho actually finished top 5 in %d" % (test_year + 1))
    real = out.nsmallest(5, "mvp_rank_next")
    for _, r in real.iterrows():
        print("   %2d. %-26s %-4s  actual %7.1f | predicted %7.1f | chance %.3f"
              % (r["mvp_rank_next"], (r["first_name"] + " " + r["last_name"])[:26],
                 r["team_abbrev"], r["mvp_score_next"], r["predicted"], r["chance"]))
    return reg, clf


# ---------------------------------------------------------------- main
def main():
    seasons = pd.read_csv(SEASONS)
    years = sorted(seasons["year"].unique())
    print("seasons %s  (%d player-seasons)\n" % (years, len(seasons)))

    weights = fit_weights(seasons)
    json.dump({"weights": weights, "baseline": BASELINE, "anchor": MVP_2025,
               "anchor_year": 2025, "top_n": TOP_N},
              io.open(os.path.join(SRC, "mvp_weights.json"), "w"), indent=1)

    pairs = make_year_pairs(seasons, weights=weights)
    print("\n%d season-to-season pairs, %d of them top-%d seasons"
          % (len(pairs), int(pairs["is_top5_next"].sum()), TOP_N))
    print("   pairs per year: %s"
          % ", ".join("%d->%d %d" % (y, y + 1, n)
                      for y, n in pairs["year"].value_counts().sort_index().items()))

    evaluate(pairs, test_year=max(pairs["year"]), weights=weights)
    cross_validate_by_year(pairs, weights)

    # ---- refit on everything and forecast the season after the data ends -------
    last, nxt = years[-1], years[-1] + 1
    print("\n" + "=" * 74)
    print("FINAL MODEL: refit on all %d pairs, forecasting %d from %d"
          % (len(pairs), nxt, last))
    print("=" * 74)

    reg = build_score_pipeline(weights=weights, random_state=SEED)
    reg.fit(pairs, pairs["mvp_score_next"])
    clf = build_chance_pipeline(weights=weights, random_state=SEED)
    clf.fit(pairs, chance_targets(pairs))

    current = seasons[seasons["year"] == last].copy()
    current["mvp_score"] = mvp_score(current, weights)
    current["predicted_mvp_score_next"] = reg.predict(current)
    current["mvp_chance_next"] = clf.predict_proba(current)[:, 1]
    current["predicted_rank_next"] = current["predicted_mvp_score_next"].rank(
        ascending=False, method="min").astype(int)

    print("\nprojected %d MVP race - top 15 by predicted score" % nxt)
    leaderboard(current, "predicted_mvp_score_next", 15,
                extra=("mvp_chance_next", "mvp_score"))
    print("\n   (columns: predicted %d score | chance of a top-%d %d season | actual %d score)"
          % (nxt, TOP_N, nxt, last))

    print("\nhighest chance of a top-%d %d season" % (TOP_N, nxt))
    leaderboard(current, "mvp_chance_next", 10, extra=("predicted_mvp_score_next",))

    cols = ["player_id", "first_name", "last_name", "team_abbrev", "team", "division",
            "mvp_score", "predicted_mvp_score_next", "mvp_chance_next", "predicted_rank_next"]
    out = os.path.join(SRC, "mvp_predictions_%d.csv" % nxt)
    current.sort_values("predicted_mvp_score_next", ascending=False)[cols].to_csv(
        out, index=False, encoding="utf-8-sig")
    # sklearn_version travels with the artifact so modal_serve.py can pin the
    # image to exactly what built it, instead of a number typed by hand that
    # someone has to remember to change after retraining elsewhere.
    import sklearn
    joblib.dump({"score_model": reg, "chance_model": clf, "weights": weights,
                 "trained_through": last, "top_n": TOP_N,
                 "sklearn_version": sklearn.__version__},
                os.path.join(SRC, "pipeline.joblib"))
    print("\nwrote %s, pipeline.joblib, mvp_weights.json" % os.path.basename(out))


if __name__ == "__main__":
    main()
