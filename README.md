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
| `supabase/schema.sql` | the four tables the dashboard reads |
| `supabase/drop_legacy.sql` | removes the `2025 Player Dataset` CSV import these tables replace |
| `push_supabase.py` | loads the dataset into those tables |
| `env.py` | reads `.env` for the two scripts above |
| `fetch.py` | pulls games and per-game player stats from the UFA API |
| `build2.py` | aggregates the raw JSON into the dataset |
| `ranks.py` | computes league-wide ranks |
| `fit_mvp.py` | grid-searches the MVP Score weights |
| `export_csv.py` | writes the four CSVs |
| `ufa_2025_*.csv` | teams, games, player-season and player-week tables |

## Supabase

Four tables, all public read-only under row-level security:

| Table | Rows | Holds |
|---|---|---|
| `season_meta` | 1 | week structure, league-wide counts, the fitted MVP weights |
| `metrics` | 5 | the five races — label, description, lane length, week range |
| `players` | 32 | season totals, league ranks, week-by-week trends, headshot |
| `awards` | 3 | MVP, DPOY, ROY and their citations |

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
read the four tables and nothing else.

On load the page fetches all four tables from PostgREST in parallel and reshapes
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
