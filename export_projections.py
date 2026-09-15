# -*- coding: utf-8 -*-
"""Export next-season projections for the dashboard's player picker.

train_mvp.py fits the two random forests and saves them to pipeline.joblib. This
applies them to every player with a real workload in the most recent season and
writes the result three ways:

  ufa_projections_<year>.csv   the table, for Supabase (push_supabase.py loads it)
  <script id="ufa-projections"> injected into ufa-mvp-race.html, so the page still
                               works as a Claude Artifact, where fetches are blocked

Players below MIN_POINTS are dropped. The cut is not about the model - it predicts a
near-zero score for a fringe player perfectly well - but about the picker: 800 names
where 350 of them played a handful of points makes the control worse, not better.

    python train_mvp.py         # first, to fit and save the models
    python export_projections.py
"""
import io
import json
import os
import re
import sys

import joblib
import pandas as pd

from pipeline_def import mvp_score

HERE = os.path.dirname(os.path.abspath(__file__))
SEASONS = os.path.join(HERE, "ufa_player_season_2021_2026.csv")
MODELS = os.path.join(HERE, "pipeline.joblib")
HTML = os.path.join(HERE, "ufa-mvp-race.html")
MIN_POINTS = 100
BLOB_ID = "ufa-projections"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass


def project():
    """-> (DataFrame of projections, season projected from, season projected to)."""
    if not os.path.exists(MODELS):
        raise SystemExit("no pipeline.joblib - run train_mvp.py first")
    bundle = joblib.load(MODELS)
    reg, clf = bundle["score_model"], bundle["chance_model"]
    weights, last = bundle["weights"], bundle["trained_through"]

    seasons = pd.read_csv(SEASONS)
    cur = seasons[seasons["year"] == last].copy()
    cur["points_played"] = cur["o_points_played"] + cur["d_points_played"]
    cur = cur[cur["points_played"] >= MIN_POINTS].copy()

    cur["mvp_score"] = mvp_score(cur, weights)
    cur["projected"] = reg.predict(cur)
    cur["chance"] = clf.predict_proba(cur)[:, 1]
    cur = cur.sort_values("projected", ascending=False).reset_index(drop=True)
    cur["projected_rank"] = cur.index + 1

    print("%d of %d %d players cleared %d points played"
          % (len(cur), (seasons["year"] == last).sum(), last, MIN_POINTS))
    return cur, last, last + 1


def text(v):
    """Blank rather than NaN. At least one UFA player is a mononym with no first
    name, and a bare NaN in the page's JSON blob is not parseable JSON."""
    return "" if v is None or (isinstance(v, float) and v != v) else str(v).strip()


def rows(cur, last):
    """Compact records for the page. Keys are short because they ship in the HTML."""
    out = []
    for _, r in cur.iterrows():
        record = "%d-%d" % (r["team_wins"], r["team_losses"])
        if r["team_ties"]:
            record += "-%d" % r["team_ties"]
        out.append({
            "id": r["player_id"],
            "first": text(r["first_name"]),
            "last": text(r["last_name"]),
            "abbr": text(r["team_abbrev"]),
            "team": text(r["team"]),
            "div": text(r["division"]),
            "record": record,
            "pts": int(r["points_played"]),
            "prev": round(float(r["mvp_score"]), 1),
            "proj": round(float(r["projected"]), 1),
            "chance": round(float(r["chance"]), 4),
            "rank": int(r["projected_rank"]),
        })
    return out


def inject(payload):
    """Replace (or add) the projections blob in the Artifact fragment."""
    html = io.open(HTML, encoding="utf-8").read()
    blob = ('<script type="application/json" id="%s">%s</script>'
            % (BLOB_ID, json.dumps(payload, ensure_ascii=False, separators=(",", ":"))))

    pattern = r'<script type="application/json" id="%s">.*?</script>' % BLOB_ID
    if re.search(pattern, html, re.S):
        html = re.sub(pattern, lambda _: blob, html, count=1, flags=re.S)
        how = "replaced"
    else:
        # sit it next to the season blob it belongs with
        anchor = '<div id="root"></div>'
        if anchor not in html:
            raise SystemExit("could not find %s in %s" % (anchor, os.path.basename(HTML)))
        html = html.replace(anchor, blob + "\n" + anchor, 1)
        how = "inserted"

    io.open(HTML, "w", encoding="utf-8", newline="\n").write(html)
    print("%s <script id=\"%s\"> in %s  (%.0f KB blob)"
          % (how, BLOB_ID, os.path.basename(HTML), len(blob) / 1024))


def main():
    cur, last, nxt = project()
    recs = rows(cur, last)
    payload = {"from": int(last), "to": int(nxt), "minPoints": MIN_POINTS,
               "players": recs}
    # the blob is parsed by JSON.parse in the browser, which has no NaN literal
    json.loads(json.dumps(payload, allow_nan=False))

    out = os.path.join(HERE, "ufa_projections_%d.csv" % nxt)
    pd.DataFrame(recs).to_csv(out, index=False, encoding="utf-8-sig")
    print("wrote %s  (%d rows)" % (os.path.basename(out), len(recs)))
    inject(payload)

    print("\nprojected %d leaders" % nxt)
    for r in recs[:10]:
        print("   %2d. %-26s %-4s  %6.1f   chance %.3f   (%d: %.1f)"
              % (r["rank"], (r["first"] + " " + r["last"])[:26], r["abbr"],
                 r["proj"], r["chance"], last, r["prev"]))


if __name__ == "__main__":
    main()
