# UFA MVP Race

A dashboard of the 2025 Ultimate Frisbee Association season, built from the league's
public stats backend. Five races run down an ultimate field: plus/minus, total scores,
blocks and receiving yards over the regular season, plus a fitted **MVP Score** across
the full season including playoffs.

Every counting statistic is real, aggregated game by game from
`backend.ufastats.com` — the same feed that powers watchufa.com. MVP Score is the one
modelled figure and is labelled as such on the page.

## Layout

| Path | What it is |
|---|---|
| `site/index.html` | the deployed page — a single self-contained file, no build step |
| `ufa-mvp-race.html` | the same markup as a Claude Artifact fragment (no `<head>`) |
| `make_site.py` | wraps the fragment into `site/index.html` |
| `fetch.py` | pulls games and per-game player stats from the UFA API |
| `build2.py` | aggregates the raw JSON into the dataset embedded in the page |
| `ranks.py` | computes league-wide ranks |
| `fit_mvp.py` | grid-searches the MVP Score weights |
| `export_csv.py` | writes the four CSVs |
| `ufa_2025_*.csv` | teams, games, player-season and player-week tables |

## Rebuilding

```bash
python fetch.py        # downloads games.json, teams.json and pg/*.json
python build2.py       # rebuilds the embedded dataset
python make_site.py    # regenerates site/index.html
```

## Deploying

Static, zero build. `vercel.json` points Vercel at `site/`.

```bash
vercel deploy --prod
```
