# UFA MVP Race

A dashboard of the 2025 Ultimate Frisbee Association season, built from the league's
public stats backend. Five races run down an ultimate field: plus/minus, total scores,
blocks and receiving yards over the regular season, plus a fitted **MVP Score** across
the full season including playoffs.

Every counting statistic is real, aggregated game by game from
`backend.ufastats.com` — the same feed that powers watchufa.com. MVP Score is the one
modelled figure and is labelled as such on the page.

The season is stored in **Supabase**, and the page reads it at load time.

## Layout

| Path | What it is |
|---|---|
| `site/index.html` | the deployed page — a single self-contained file, no build step |
| `ufa-mvp-race.html` | the same markup as a Claude Artifact fragment (no `<head>`) |
| `make_site.py` | wraps the fragment into `site/index.html` and bakes in the Supabase credentials |
| `supabase/schema.sql` | the five tables the dashboard reads |
| `supabase/drop_legacy.sql` | removes the `2025 Player Dataset` CSV import these tables replace |
| `push_supabase.py` | loads the dataset into those tables |
| `env.py` | reads `.env` for the two scripts above |
| `fetch.py` | pulls games and per-game player stats from the UFA API |
| `build2.py` | aggregates the raw JSON into the dataset |
| `ranks.py` | computes league-wide ranks |
| `fit_mvp.py` | grid-searches the MVP Score weights |
| `export_csv.py` | writes the four CSVs |
| `ufa_2025_*.csv` | teams, games, player-season and player-week tables |
| `pipeline_def.py` | the MVP feature transformer and the two random-forest pipelines |
| `train_mvp.py` | fits, evaluates and applies those pipelines |
| `fetch_seasons.py` | pulls 2021–2026 season totals for the model to train on |
| `export_projections.py` | scores the picker's 451 players and writes them into the page |
| `serve.py` | the FastAPI app: endpoints, schemas, `X-API-Key` auth, Supabase reader |
| `pipeline.joblib` | the two fitted pipelines, their weights, and the version stamp |
| `modal_serve.py` | deploys `serve.py` to Modal, with the cost caps |
| `supabase/functions/mvp-proxy/` | edge function that holds the API key for the browser |
| `test_pipeline_def.py` | tests for `pipeline_def.py` |
| `test_serve.py` | tests for the app in `serve.py` |
| `test_store.py` | tests for the Supabase reader in `serve.py` |

## Predicting next season

The dashboard scores a season that has already happened. `pipeline_def.py` answers
the next question: given a player's season, **what will their MVP Score be next year,
and what are their chances of an MVP-calibre season?**

```bash
pip install scikit-learn
python fetch_seasons.py      # writes ufa_player_season_2021_2026.csv (cached in seasons/)
python train_mvp.py          # evaluates, then forecasts the next season
python export_projections.py # scores the picker's players into the page
python test_pipeline_def.py
```

### The pieces

`MVPFeatureTransformer` is a scikit-learn transformer (`BaseEstimator`,
`TransformerMixin`; `__init__` only assigns its arguments, `fit` returns `self`). It
turns raw season totals into ~50 numeric features: the counting stats already in the
data, plus per-point rates, efficiency ratios, team record, and the MVP Score itself.
It learns nothing from the values — `fit` only records the column layout — so it is
safe to fit on one season and apply to another.

Two pipelines wrap it around a random forest:

| Builder | Model | Predicts |
|---|---|---|
| `build_score_pipeline()` | `RandomForestRegressor` | next season's MVP Score |
| `build_chance_pipeline()` | `RandomForestClassifier` | `predict_proba` → chance of a top-5 MVP Score next season |

`make_year_pairs()` joins each player-season to the same player's next season, which
is where both targets come from. Training uses the 2,806 such pairs in 2021–2026;
2020 is absent because there was no season.

### How well it works

Evaluated by holding out the most recent season pair entirely, and again by
leave-one-season-out across all five. The comparison that matters is the
**persistence baseline** — simply assuming next season looks like this one.

| | random forest | persistence |
|---|---|---|
| next-season score, MAE | **17.7 ± 1.0** | 18.4 ± 1.0 |
| next-season score, R² | **0.45 ± 0.02** | 0.32 ± 0.07 |
| top-5 ROC-AUC | **0.95 ± 0.05** | 0.93 ± 0.07 |
| top-5 average precision | **0.35 ± 0.21** | 0.30 ± 0.19 |

The regression is a real improvement: it explains about 45% of the variance in next
season's score against the baseline's 32%, on every fold.

**The chance model took two attempts.** A `RandomForestClassifier` trained directly
on the top-5 label scored **worse than the baseline** (AP 0.283 vs 0.303) — not a
tuning problem but a data one: a top-5 season is about 1 row in 130, so all six
seasons together hold 21 positive examples, which will not support a decision
boundary over 50 features.

What replaced it, `CalibratedChance`, puts the signal where the data is. The random
forest predicts the continuous score, where all 2,806 rows are informative, and a
one-parameter logistic regression maps that prediction to P(top-5). The calibrator is
fitted on out-of-fold predictions so it never sees the forest's own fitted values.
That beats the baseline on both metrics and, unlike a rank, returns an actual
probability. Alternatives measured and rejected: a classifier on a top-25 label
(equal AP, but it answers a different question), and gradient boosting (AP 0.162).

The fold-to-fold spread is still wide (±0.21 on AP). Twenty-one positives is more
than a forest can learn a boundary from and fewer than anyone should want.

The forecast, which comes with all of that attached, is in
`mvp_predictions_2027.csv`; `pipeline.joblib` holds the fitted pipelines.

### On the page

The dashboard has a **Pick five** panel: choose any five players from the 451 who played
at least 100 points in 2026, and it ranks them by projected 2027 MVP Score, showing each
one's top-five chance and their actual 2026 score alongside. Both caveats above are
restated in the methodology section directly below it.

No model runs in the browser. A player's 2026 stat line is fixed, so the projection is
fixed too — `export_projections.py` scores all 451 in Python and ships the results, the
same way every other number on the page is precomputed. It writes them twice:
`ufa_projections_2027.csv` for the `projections` table in Supabase, and a
`<script id="ufa-projections">` blob inside `ufa-mvp-race.html` so the page still works
as a Claude Artifact, where outbound fetches are blocked.

Adding it to a deployment that already exists takes three steps — the `projections`
table is new, so the page falls back to its embedded blob until you create it:

```bash
# 1. run the projections block of supabase/schema.sql in the SQL editor
python push_supabase.py   # 2. loads projections along with the other four tables
python make_site.py       # 3. regenerates site/index.html
```

## The prediction API

The models also run as a service on [Modal](https://modal.com), behind FastAPI. Modal
scales to zero, so an idle deployment costs nothing; an API key keeps it that way.

**Rows come from Supabase, not from a file.** The projections and the completed-season
totals are read over PostgREST at request time and cached for five minutes, so loading
new numbers is `python push_supabase.py` with no redeploy. Only the model bundle ships
inside the image, because it is a build artifact rather than data.

```bash
pip install "fastapi[standard]" uvicorn
python test_store.py               # 20 tests, no network
python test_serve.py               # 30 tests, no server, no Modal, no Supabase
uvicorn serve:app --reload         # then open http://127.0.0.1:8000/docs
```

Three files ship in the image and nothing else - `serve.py`, `pipeline_def.py` and
`pipeline.joblib`. That is why the Supabase reader lives inside `serve.py` rather
than in a module of its own: it would have been a fourth. `pipeline_def.py` is not
optional even though `modal_serve.py` never imports it, because unpickling an sklearn
`Pipeline` rebuilds the custom transformer by module path, so the class has to be
importable inside the container or the load fails.

Both suites stub the database with a fake opener that feeds the real client
PostgREST-shaped rows, so they run offline against nothing provisioned - but the
rename, the numeric coercion and the paging are all still under test.

`serve.py` is plain FastAPI with no Modal import, so it runs anywhere and is testable
under `TestClient`. `modal_serve.py` is the deployment wrapper, and it imports `serve`
*inside* the Modal function rather than at module scope - the deploying machine does
not need FastAPI installed to push an image.

Running it locally needs `SUPABASE_URL` and `SUPABASE_ANON_KEY` in `.env` - the same
two values the dashboard already uses - plus `MVP_API_KEY`. The anon key is the right
one: `schema.sql` grants `anon` a read-only `SELECT` on these tables and the API never
writes. The `service_role` key must never go anywhere near it.

### Endpoints

| | Auth | Does |
|---|---|---|
| `GET /health` | — | liveness, row counts, and whether the stored season is stale |
| `GET /players` | key | the projectable roster, with `search` and `limit` |
| `GET /players/{id}` | key | one player's stored projection |
| `GET /season/players` | key | the completed season's totals, best MVP Score first |
| `GET /season/players/{id}` | key | one player's completed season, ranks and trend |
| `POST /predict` | key | **any season stat line** — real or invented — projected |
| `POST /compare` | key | two to five players ranked, winner named |
| `/docs`, `/redoc`, `/openapi.json` | — | OpenAPI 3.1 docs with an Authorize button |

`/predict` takes raw season totals and returns `projected_score`, a calibrated
`chance_top5`, the `current_score` of the line you sent, and `typical_error` — the
17.7 that everything above is about. `/compare` adds `margin` and `within_noise`, true
when the gap between first and second is smaller than that error, which it usually is.

`/players` reads the `projections` table - next season. `/season/players` reads
`players` - the season that has already happened, the one the dashboard charts, with
per-metric `ranks`, weekly `trend` series and the headshot. The two are different
tables and different seasons; `/health` reports the row count of each.

When Supabase is unreachable, every data route returns **503 with the reason**, and
`/health` goes down with them. It does not fall back to a bundled copy: an API that
quietly serves last month's numbers as if they were live is worse than one that says
it is broken. `/predict` and `/compare` on custom stat lines keep working, since
those are pure model and never touch the database.

### Auth

One shared secret in an `X-API-Key` header, compared in constant time, declared as an
OpenAPI security scheme so `/docs` gets an **Authorize** button. Without it the API is
a public endpoint that spends your Modal credits, so the check is structural: the key
hangs off the `APIRouter` that carries every data route, not off the individual
endpoints. A new route is protected by where it is declared rather than by whoever
adds it remembering to ask, and `test_serve.py` walks the live OpenAPI spec and fails if
anything but `/health` is reachable without a key. An empty `MVP_API_KEY` means 503,
never "no key needed". Generate a key with:

```bash
python -c "import secrets; print('ufa_' + secrets.token_urlsafe(32))"
```

It belongs in three places and nowhere else — `.env` (gitignored), a Modal Secret, and
a Supabase secret. It must never reach the browser; see the proxy below for why.

### Deploying

Two secrets, because they have different lifetimes and different blast radii - the
API key is yours to rotate, the Supabase credentials belong to the database:

```bash
modal secret create ufa-mvp-api-key MVP_API_KEY=<key>              # once
modal secret create ufa-supabase SUPABASE_URL=https://<ref>.supabase.co \
                                 SUPABASE_KEY=<anon key>           # once
modal serve modal_serve.py     # temporary URL, live-reloads, dies on Ctrl-C
modal deploy modal_serve.py    # permanent URL
```

scikit-learn is pinned to the version recorded **inside `pipeline.joblib`**, not to a
number typed into the deploy script. sklearn stamps `_sklearn_version` into every
estimator it pickles and `train_mvp.py` also writes it as a plain `sklearn_version`
key, so `modal_serve.py` reads the pin off the artifact that will actually be loaded.
A hand-written pin is a number someone has to remember to change after retraining
elsewhere, and a version mismatch can unpickle an estimator into something subtly
different rather than failing outright - so this one is load-bearing.

Note that Modal re-imports `modal_serve.py` *inside* the container to find the
function, so its module scope runs in both places. In the container the artifact
sits at `MVP_MODEL_PATH` rather than beside the script and the image is already
pinned, so `pinned_sklearn()` reports the installed version there instead of
re-reading a 20 MB pickle on every cold start.

Cost controls are in `modal_serve.py`: `min_containers=0` (scale to zero),
`scaledown_window=60`, `max_containers=2` so a spike or a scraper cannot fan out, CPU
only, and the model baked into the image so cold starts download nothing. Note that
`/health` is unauthenticated and now reads Supabase as well as waking a container -
poll it every few minutes, not every ten seconds.

New *numbers* need no deploy at all - `python push_supabase.py`, and a warm container
picks them up within `MVP_CACHE_SECONDS` (300 by default). Only a new *model* needs
one: `python train_mvp.py && python export_projections.py && python push_supabase.py
&& modal deploy modal_serve.py`.

The deployment reads Supabase, so it cannot start serving until `supabase/schema.sql`
has been applied and `push_supabase.py` has run at least once. That is the cost of a
single source of truth, and it is the same prerequisite the dashboard already has.

### Deployed

```
https://jbecke20--ufa-mvp-api-fastapi-app.modal.run
```

`/docs` is public and browsable; everything else needs the key. The Supabase
credentials come from the `ufa-supabase` secret - deliberately its own, not the
workspace's older `supabase-credentials`, which holds a different project's URL.
Pointing at that one returns a PostgREST 404 that reads exactly like an unapplied
schema, which is a confusing way to spend an afternoon.

### Why the page goes through Supabase

The dashboard's Pick five panel calls the model live, but **an API key shipped to a
browser is a public API key** — anyone can read it out of the page and spend your Modal
credits. So the page calls `supabase/functions/mvp-proxy`, an edge function that holds
the key server-side and forwards the request. It allowlists three routes, caps the body
at 8 KB, and rate-limits per IP.

```bash
supabase secrets set MODAL_API_KEY=<key>
supabase secrets set MODAL_API_URL=https://<workspace>--ufa-mvp-api-fastapi-app.modal.run
supabase functions deploy mvp-proxy --no-verify-jwt
```

Every failure path — no proxy, cold container, no network — falls back to the
projections embedded in the page at build time. Same model, computed earlier. The badge
beside the panel heading reads **live model** or **snapshot** so you can tell which you
are looking at, and the panel keeps working offline and as a Claude Artifact.

## Supabase

Five tables, all public read-only under row-level security:

| Table | Rows | Holds |
|---|---|---|
| `season_meta` | 1 | week structure, league-wide counts, the fitted MVP weights |
| `metrics` | 5 | the five races — label, description, lane length, week range |
| `players` | 32 | season totals, league ranks, week-by-week trends, headshot |
| `awards` | 3 | MVP, DPOY, ROY and their citations |
| `projections` | 451 | next-season projections for the Pick five panel |

### Setting it up

1. Create a project at [supabase.com](https://supabase.com).
2. Copy `.env.example` to `.env` and fill in the three values from
   **Project Settings → API**. `.env` is gitignored; only the example is tracked.
3. Run `supabase/schema.sql` in the SQL editor.
4. Load the data:

   ```bash
   python push_supabase.py
   ```

   It reads `ufa2025.json` when `build2.py` has produced one, and otherwise the
   snapshot embedded in `ufa-mvp-race.html` — so a fresh clone can seed the
   tables without re-downloading the season. Writes use the `service_role` key
   and upsert, so re-running is safe.
5. Once the page is loading from Supabase, run `supabase/drop_legacy.sql` to
   remove the `2025 Player Dataset` CSV import these four tables replace. It is
   deliberately a separate step: don't drop the old table until the new ones are
   proven. `ufa_2025_player_week.csv` in this repo is the same 8,445 rows if you
   ever need it back.

### How the page reads it

`make_site.py` writes the project URL and the **anon** key into
`site/index.html` as `window.UFA_SUPABASE`. That key is public by design: the
schema enables row-level security with a select-only policy, so the browser can
read the five tables and nothing else.

On load the page fetches all five tables from PostgREST in parallel and reshapes
the snake_case columns into the keys the components use. If Supabase is
unreachable — or unconfigured, which is the case inside a Claude Artifact, whose
sandbox blocks outbound fetches — it falls back to the frozen snapshot in the
`<script id="ufa-data">` blob and logs why to the console.

## Rebuilding

```bash
python fetch.py         # downloads games.json, teams.json and pg/*.json
python build2.py        # rebuilds ufa2025.json
python push_supabase.py # pushes it to Supabase
python make_site.py     # regenerates site/index.html
```

## Deploying

Static, zero build. `vercel.json` points Vercel at `site/`.

```bash
vercel deploy --prod
```

Run `make_site.py` before deploying so the credentials are current. If you'd
rather keep them out of the repo entirely, set `SUPABASE_URL` and
`SUPABASE_ANON_KEY` as environment variables in CI — real environment variables
take precedence over `.env`.
