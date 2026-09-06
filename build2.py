# -*- coding: utf-8 -*-
"""Rebuild the dashboard dataset, adding the fitted MVP Score as a fifth race.

Four races stay on the regular season (weeks 1-13). MVP Score runs the full
season (weeks 1-16) because no regular-season-only weighting reproduces the
actual ballot - see fit_mvp.py / fit_reg.py.
"""
import json, glob, os, collections, io

SRC = os.path.dirname(os.path.abspath(__file__))
games = {g["gameID"]: g for g in json.load(io.open(os.path.join(SRC, "games.json"), encoding="utf-8"))["data"]}
teams = {t["teamID"]: t for t in json.load(io.open(os.path.join(SRC, "teams.json"), encoding="utf-8"))["data"]}
ALLSTAR = {"allstars1", "allstars2"}
REG_W  = list(range(1, 14))
FULL_W = list(range(1, 17))
W = [1.0, 1.5, 1.0, 1.0, -1.0, 1.0, 1.0]      # fitted; only rec-yards moves off neutral

prev = json.load(io.open(os.path.join(SRC, "ufa2025.json"), encoding="utf-8"))
IMG = {p["id"]: p.get("img") for p in prev["players"]}

reg  = collections.defaultdict(lambda: collections.defaultdict(collections.Counter))  # pid->wk->stats
mvpw = collections.defaultdict(collections.Counter)                                   # pid->wk->mvp pts
meta, tg = {}, collections.defaultdict(collections.Counter)

for f in glob.glob(os.path.join(SRC, "pg", "*.json")):
    gid = os.path.basename(f)[:-5]
    g = games[gid]
    if not g["week"]: continue
    wk = int(g["week"].split("-")[1])
    for p in json.load(io.open(f, encoding="utf-8"))["data"]:
        if p["teamID"] in ALLSTAR: continue
        pid = p["player"]["playerID"]
        meta[pid] = (p["player"]["firstName"].strip(), p["player"]["lastName"].strip(),
                     (p.get("jerseyNumber") or "").strip())
        tg[pid][p["teamID"]] += 1
        gv = lambda k: p.get(k) or 0
        home = p["teamID"] == g["homeTeamID"]
        won = (g["homeScore"] > g["awayScore"]) if home else (g["awayScore"] > g["homeScore"])
        comp = [gv("goals") + gv("assists") + gv("hockeyAssists"),
                gv("yardsReceived") / 100.0, gv("yardsThrown") / 100.0, gv("blocks"),
                gv("throwaways") + gv("stalls") + gv("drops"), gv("completions") / 100.0,
                1.0 if won else 0.0]
        mvpw[pid][wk] += sum(W[i] * comp[i] for i in range(7))
        if wk <= 13:
            c = reg[pid][wk]
            c["games"] += 1
            for k, v in p.items():
                if isinstance(v, (int, float)): c[k] += v

team_of = {pid: c.most_common(1)[0][0] for pid, c in tg.items()}
season = {pid: sum(wks.values(), collections.Counter()) for pid, wks in reg.items()}
pm = lambda c: c["goals"] + c["assists"] + c["blocks"] - c["throwaways"] - c["stalls"] - c["drops"]
sc = lambda c: c["goals"] + c["assists"]
mvp_total = {pid: round(sum(mvpw[pid].values()), 1) for pid in mvpw}

METRICS = [
  {"k":"mvp","label":"MVP Score","short":"MVP","ceil":250,"weeks":FULL_W,"season":"full",
   "desc":"A fitted composite: scores + 1.5×(receiving yd/100) + throwing yd/100 + blocks "
          "− turnovers + completions/100 + team wins, run across the full season."},
  {"k":"pm","label":"Plus / minus","short":"+/-","ceil":100,"weeks":REG_W,"season":"reg",
   "desc":"Goals + assists + blocks, minus throwaways, drops and stalls — the UFA's headline composite."},
  {"k":"sc","label":"Total scores","short":"G+A","ceil":100,"weeks":REG_W,"season":"reg",
   "desc":"Goals plus assists: every point the player finished or set up."},
  {"k":"blocks","label":"Blocks","short":"Blocks","ceil":30,"weeks":REG_W,"season":"reg",
   "desc":"Defensive blocks — the currency of the Defensive Player of the Year ballot."},
  {"k":"ry","label":"Receiving yards","short":"Rec yd","ceil":4000,"weeks":REG_W,"season":"reg",
   "desc":"Yards gained catching the disc over the regular season."},
]
GET = {"pm": pm, "sc": sc, "blocks": lambda c: c["blocks"], "ry": lambda c: c["yardsReceived"]}

def trends(pid):
    out = {}
    for m in METRICS:
        run, arr = 0.0, []
        for w in m["weeks"]:
            if m["k"] == "mvp":
                run += mvpw[pid].get(w, 0.0)
            elif w in reg[pid]:
                run += GET[m["k"]](reg[pid][w])
            arr.append(round(run, 1))
        out[m["k"]] = arr
    return out

allp = list(season)
ranks = collections.defaultdict(dict)
for m in METRICS:
    v = {pid: (mvp_total.get(pid, 0) if m["k"] == "mvp" else GET[m["k"]](season[pid])) for pid in allp}
    first = {}
    for i, pid in enumerate(sorted(allp, key=lambda p: -v[p])):
        first.setdefault(v[pid], i + 1)
        ranks[pid]["rank_" + m["k"]] = first[v[pid]]
        ranks[pid][m["k"]] = first[v[pid]]

pool = []
for m in METRICS:
    key = (lambda p: mvp_total.get(p, 0)) if m["k"] == "mvp" else (lambda p, m=m: GET[m["k"]](season[p]))
    for pid in sorted(allp, key=lambda p: -key(p))[:10]:
        if pid not in pool: pool.append(pid)
for pid in ("tdecraene", "ddemarre", "tjohnson1"):
    if pid not in pool: pool.append(pid)

out = []
for pid in pool:
    f, l, j = meta[pid]; t = teams[team_of[pid]]; c = season[pid]
    out.append({
        "id": pid, "first": f, "last": l, "num": j,
        "team": t["fullName"], "abbr": t["abbrev"], "div": t["division"]["name"],
        "record": "%d-%d" % (t["wins"], t["losses"]),
        "gp": c["games"], "gpFull": len(mvpw[pid]) and sum(1 for _ in ()) or 0,
        "g": c["goals"], "a": c["assists"], "ha": c["hockeyAssists"], "blocks": c["blocks"],
        "cmp": c["completions"], "att": c["throwAttempts"],
        "to": c["throwaways"] + c["stalls"] + c["drops"],
        "ty": c["yardsThrown"], "ry": c["yardsReceived"],
        "pm": pm(c), "sc": sc(c), "mvp": mvp_total.get(pid, 0),
        "rank": {m["k"]: ranks[pid][m["k"]] for m in METRICS},
        "trend": trends(pid), "img": IMG.get(pid),
    })

data = {
  "weeks": REG_W, "regWeeks": 13, "playoffFrom": 14,
  "metrics": [{k: m[k] for k in ("k","label","short","desc","ceil","weeks","season")} for m in METRICS],
  "players": out,
  "awards": prev["awards"],
  "leagueN": len(allp),
  "games": sum(1 for g in games.values() if g["week"] and int(g["week"].split("-")[1]) <= 13),
  "gamesFull": sum(1 for g in games.values() if g["week"]),
  "fit": {"weights": W,
          "names": ["scores","rec yd/100","thr yd/100","blocks","turnovers","completions/100","team wins"],
          "solutions": 21099},
}
json.dump(data, io.open(os.path.join(SRC, "ufa2025.json"), "w", encoding="utf-8"),
          ensure_ascii=False, separators=(",", ":"))

board = sorted(pool, key=lambda p: -mvp_total.get(p, 0))[:8]
print("pool %d | league %d | reg games %d | full games %d"
      % (len(pool), data["leagueN"], data["games"], data["gamesFull"]))
print("\nMVP Score board:")
for i, pid in enumerate(board):
    print("  %d. %-24s %6.1f   wk13=%6.1f  wk16=%6.1f"
          % (i + 1, meta[pid][0] + " " + meta[pid][1], mvp_total[pid],
             trends(pid)["mvp"][12], trends(pid)["mvp"][15]))
print("\nDecraene ranks:", {m["k"]: ranks["tdecraene"][m["k"]] for m in METRICS})
print("size", os.path.getsize(os.path.join(SRC, "ufa2025.json")))
