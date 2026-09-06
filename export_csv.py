# -*- coding: utf-8 -*-
"""Export the 2025 UFA regular season to tidy CSVs.

Reads the raw JSON pulled by fetch.py (games.json, teams.json, pg/*.json) and
writes four files:

  ufa_2025_teams.csv          one row per team
  ufa_2025_games.csv          one row per regular-season game
  ufa_2025_player_season.csv  one row per player, season totals + league ranks
  ufa_2025_player_week.csv    one row per player per week they played (long format)

Regular season only: weeks 1-13. Playoffs (weeks 14-16) and the all-star game
are excluded, matching the dashboard.
"""
import json, glob, os, csv, collections, io

SRC = os.path.dirname(os.path.abspath(__file__))
OUT = r"C:\users\jbeck\onedrive\desktop\DTSC-3601\assignment2"
REG = {"week-%d" % i for i in range(1, 14)}

games = {g["gameID"]: g for g in json.load(io.open(os.path.join(SRC, "games.json"), encoding="utf-8"))["data"]}
teams = {t["teamID"]: t for t in json.load(io.open(os.path.join(SRC, "teams.json"), encoding="utf-8"))["data"]}

# raw counting stats carried straight through from the API
RAW = ["goals", "assists", "hockeyAssists", "blocks", "callahans", "callahansThrown",
       "completions", "throwAttempts", "throwaways", "stalls", "drops", "catches",
       "yardsThrown", "yardsReceived", "hucksCompleted", "hucksAttempted",
       "oPointsPlayed", "oPointsScored", "dPointsPlayed", "dPointsScored",
       "pulls", "obPulls", "secondsPlayed"]
SNAKE = {"hockeyAssists": "hockey_assists", "callahansThrown": "callahans_thrown",
         "throwAttempts": "throw_attempts", "yardsThrown": "yards_thrown",
         "yardsReceived": "yards_received", "hucksCompleted": "hucks_completed",
         "hucksAttempted": "hucks_attempted", "oPointsPlayed": "o_points_played",
         "oPointsScored": "o_points_scored", "dPointsPlayed": "d_points_played",
         "dPointsScored": "d_points_scored", "obPulls": "ob_pulls",
         "secondsPlayed": "seconds_played"}
snake = lambda k: SNAKE.get(k, k)

# ---------------------------------------------------------------- gather
by_week = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))  # pid -> week -> stats
name, team_of, jersey = {}, {}, {}
team_games = collections.defaultdict(collections.Counter)

for f in glob.glob(os.path.join(SRC, "pg", "*.json")):
    g = games[os.path.basename(f)[:-5]]
    if g["week"] not in REG:
        continue
    wk = int(g["week"].split("-")[1])
    for p in json.load(io.open(f, encoding="utf-8"))["data"]:
        pid = p["player"]["playerID"]
        name[pid] = (p["player"]["firstName"].strip(), p["player"]["lastName"].strip())
        team_games[pid][p["teamID"]] += 1
        if (p.get("jerseyNumber") or "").strip():
            jersey[pid] = p["jerseyNumber"].strip()
        c = by_week[pid][wk]
        c["games_played"] += 1
        for k in RAW:
            c[snake(k)] += p.get(k) or 0

for pid, c in team_games.items():
    team_of[pid] = c.most_common(1)[0][0]

def derive(c):
    d = {}
    d["turnovers"] = c["throwaways"] + c["stalls"] + c["drops"]
    d["plus_minus"] = c["goals"] + c["assists"] + c["blocks"] - d["turnovers"]
    d["total_scores"] = c["goals"] + c["assists"]
    d["completion_pct"] = round(c["completions"] / c["throw_attempts"] * 100, 2) if c["throw_attempts"] else ""
    d["minutes_played"] = round(c["seconds_played"] / 60, 1)
    return d

season = {}
for pid, wks in by_week.items():
    c = collections.Counter()
    for w in wks.values():
        c.update(w)
    season[pid] = c

# ---------------------------------------------------------------- ranks
RANKED = {"plus_minus": lambda c, d: d["plus_minus"],
          "total_scores": lambda c, d: d["total_scores"],
          "goals": lambda c, d: c["goals"],
          "assists": lambda c, d: c["assists"],
          "blocks": lambda c, d: c["blocks"],
          "yards_thrown": lambda c, d: c["yards_thrown"],
          "yards_received": lambda c, d: c["yards_received"]}
ranks = collections.defaultdict(dict)
for k, fn in RANKED.items():
    vals = {pid: fn(season[pid], derive(season[pid])) for pid in season}
    order = sorted(vals, key=lambda p: -vals[p])
    first = {}
    for i, pid in enumerate(order):
        first.setdefault(vals[pid], i + 1)
        ranks[pid]["rank_" + k] = first[vals[pid]]

def w(path, header, rows):
    with io.open(os.path.join(OUT, path), "w", encoding="utf-8-sig", newline="") as fh:
        wr = csv.writer(fh)
        wr.writerow(header)
        wr.writerows(rows)
    print("%-30s %5d rows" % (path, len(rows)))

# ---------------------------------------------------------------- teams
w("ufa_2025_teams.csv",
  ["team_id", "abbrev", "city", "name", "full_name", "division", "wins", "losses", "ties", "standing"],
  [[t["teamID"], t["abbrev"], t["city"], t["name"], t["fullName"], t["division"]["name"],
    t["wins"], t["losses"], t["ties"], t["standing"]]
   for t in sorted(teams.values(), key=lambda t: (t["division"]["name"], -t["wins"]))
   if t["division"]["divisionID"] != "allstars"])

# ---------------------------------------------------------------- games
w("ufa_2025_games.csv",
  ["game_id", "week", "date", "away_team", "home_team", "away_score", "home_score", "location"],
  [[g["gameID"], int(g["week"].split("-")[1]), g["startTimestamp"][:10],
    teams[g["awayTeamID"]]["abbrev"], teams[g["homeTeamID"]]["abbrev"],
    g["awayScore"], g["homeScore"], g.get("location", "")]
   for g in sorted((g for g in games.values() if g["week"] in REG),
                   key=lambda g: g["startTimestamp"])])

# ---------------------------------------------------------------- season
STAT_COLS = [snake(k) for k in RAW]
DERIVED = ["turnovers", "plus_minus", "total_scores", "completion_pct", "minutes_played"]
RANK_COLS = ["rank_" + k for k in
             ["plus_minus", "total_scores", "goals", "assists", "blocks", "yards_thrown", "yards_received"]]
head = (["player_id", "first_name", "last_name", "team_abbrev", "team", "division", "jersey", "games_played"]
        + STAT_COLS + DERIVED + RANK_COLS)
rows = []
for pid in sorted(season, key=lambda p: -derive(season[p])["plus_minus"]):
    c, t = season[pid], teams[team_of[pid]]
    d = derive(c)
    rows.append([pid, name[pid][0], name[pid][1], t["abbrev"], t["fullName"], t["division"]["name"],
                 jersey.get(pid, ""), c["games_played"]]
                + [c[k] for k in STAT_COLS] + [d[k] for k in DERIVED]
                + [ranks[pid][k] for k in RANK_COLS])
w("ufa_2025_player_season.csv", head, rows)

# ---------------------------------------------------------------- weekly
head = (["player_id", "first_name", "last_name", "team_abbrev", "week", "games_played"]
        + STAT_COLS + ["turnovers", "plus_minus", "total_scores"]
        + ["cum_plus_minus", "cum_total_scores", "cum_blocks", "cum_yards_received"])
rows = []
for pid in sorted(season, key=lambda p: (name[p][1], name[p][0])):
    run = collections.Counter()
    for wk in sorted(by_week[pid]):
        c = by_week[pid][wk]
        d = derive(c)
        run["pm"] += d["plus_minus"]; run["sc"] += d["total_scores"]
        run["b"] += c["blocks"];      run["ry"] += c["yards_received"]
        rows.append([pid, name[pid][0], name[pid][1], teams[team_of[pid]]["abbrev"], wk, c["games_played"]]
                    + [c[k] for k in STAT_COLS]
                    + [d["turnovers"], d["plus_minus"], d["total_scores"]]
                    + [run["pm"], run["sc"], run["b"], run["ry"]])
w("ufa_2025_player_week.csv", head, rows)

print("\nplayers: %d | regular-season games: %d" % (len(season), sum(1 for g in games.values() if g["week"] in REG)))
