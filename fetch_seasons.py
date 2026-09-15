# -*- coding: utf-8 -*-
"""Pull UFA season totals for several seasons into one tidy CSV.

The 2025 CSVs in this folder are built from per-game JSON (export_csv.py). Predicting
*next* season needs more than one season, so this goes at the season-totals route
instead: `playerStats?playerIDs=...&years=YYYY` returns exactly the same counting
stats already used elsewhere in the project, but for any year.

Writes ufa_player_season_2021_2026.csv - one row per player per season - and caches
each year's raw JSON under seasons/ so re-runs cost nothing.

2020 is skipped: there was no UFA season.
"""
import json, os, io, csv, time, urllib.request, urllib.error

SRC = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(SRC, "seasons")
OUT = os.path.join(SRC, "ufa_player_season_2021_2026.csv")
YEARS = [2021, 2022, 2023, 2024, 2025, 2026]
B = "https://www.backend.ufastats.com/api/v1/"
BATCH = 60                     # playerIDs per request; the URL gets long fast


def get(path):
    for attempt in range(4):
        try:
            with urllib.request.urlopen(B + path, timeout=60) as r:
                return json.load(r)
        except Exception as e:
            print("   retry (%s) %s" % (e, path[:70]))
            time.sleep(2 * (attempt + 1))
    raise SystemExit("failed: " + path)


def cached(name, fetch):
    """Read seasons/<name>.json, or fetch it and write it first."""
    os.makedirs(CACHE, exist_ok=True)
    f = os.path.join(CACHE, name + ".json")
    if os.path.exists(f):
        return json.load(io.open(f, encoding="utf-8"))
    data = fetch()
    json.dump(data, io.open(f, "w", encoding="utf-8"), ensure_ascii=False)
    return data


# raw counting stats the API returns per player-season, in CSV order
RAW = ["goals", "assists", "hockeyAssists", "blocks", "callahans", "callahansThrown",
       "completions", "throwAttempts", "throwaways", "stalls", "drops", "catches",
       "yardsThrown", "yardsReceived", "hucksCompleted", "hucksAttempted",
       "oPointsPlayed", "oPointsScored", "dPointsPlayed", "dPointsScored",
       "pulls", "obPulls", "secondsPlayed",
       "oOpportunities", "oOpportunityScores", "dOpportunities", "dOpportunityStops"]

SNAKE = {"hockeyAssists": "hockey_assists", "callahansThrown": "callahans_thrown",
         "throwAttempts": "throw_attempts", "yardsThrown": "yards_thrown",
         "yardsReceived": "yards_received", "hucksCompleted": "hucks_completed",
         "hucksAttempted": "hucks_attempted", "oPointsPlayed": "o_points_played",
         "oPointsScored": "o_points_scored", "dPointsPlayed": "d_points_played",
         "dPointsScored": "d_points_scored", "obPulls": "ob_pulls",
         "secondsPlayed": "seconds_played", "oOpportunities": "o_opportunities",
         "oOpportunityScores": "o_opportunity_scores",
         "dOpportunities": "d_opportunities", "dOpportunityStops": "d_opportunity_stops"}
snake = lambda k: SNAKE.get(k, k)
STAT_COLS = [snake(k) for k in RAW]


def fetch_year(year):
    """-> list of row dicts for one season."""
    players = cached("players_%d" % year, lambda: get("players?years=%d" % year))["data"]
    teams = {t["teamID"]: t
             for t in cached("teams_%d" % year, lambda: get("teams?years=%d" % year))["data"]}

    ids = [p["playerID"] for p in players]
    batches = [ids[i:i + BATCH] for i in range(0, len(ids), BATCH)]
    stats = []
    for i, b in enumerate(batches):
        chunk = cached("stats_%d_%02d" % (year, i),
                       lambda b=b: get("playerStats?playerIDs=%s&years=%d" % (",".join(b), year)))
        stats.extend(chunk["data"])
    print("   %d: %d players, %d stat rows" % (year, len(players), len(stats)))

    info = {p["playerID"]: p for p in players}
    rows = []
    for s in stats:
        pid = s["player"]["playerID"]
        p = info.get(pid, {})
        # a player can appear on more than one roster in a year; keep the active one
        tms = [t for t in p.get("teams", []) if t["year"] == year]
        tm = next((t for t in tms if t.get("active")), tms[0] if tms else None)
        t = teams.get(tm["teamID"]) if tm else None

        r = {"player_id": pid,
             "year": year,
             "first_name": s["player"]["firstName"].strip(),
             "last_name": s["player"]["lastName"].strip(),
             "team_id": t["teamID"] if t else "",
             "team_abbrev": t["abbrev"] if t else "",
             "team": t["fullName"] if t else "",
             "division": t["division"]["name"] if t else "",
             "jersey": (tm.get("jerseyNumber") or "") if tm else "",
             "team_wins": t["wins"] if t else "",
             "team_losses": t["losses"] if t else "",
             "team_ties": t["ties"] if t else "",
             "team_standing": t["standing"] if t else ""}
        for k in RAW:
            r[snake(k)] = s.get(k) or 0
        rows.append(r)
    return rows


HEAD = (["player_id", "year", "first_name", "last_name", "team_id", "team_abbrev", "team",
         "division", "jersey", "team_wins", "team_losses", "team_ties", "team_standing"]
        + STAT_COLS)


def main():
    rows = []
    print("fetching seasons %s" % YEARS)
    for y in YEARS:
        rows.extend(fetch_year(y))
    rows.sort(key=lambda r: (r["year"], r["last_name"], r["first_name"]))
    with io.open(OUT, "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=HEAD)
        wr.writeheader()
        wr.writerows(rows)
    print("\nwrote %s  (%d rows)" % (os.path.basename(OUT), len(rows)))


if __name__ == "__main__":
    main()
