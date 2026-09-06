# -*- coding: utf-8 -*-
"""Fit an additive per-game MVP score whose season total ranks the real 2025 MVP first.

The score has to stay additive (a running point total) so it can be raced week by week.
We grid-search the coefficients and, among every weighting that reproduces the actual
ballot, keep the one CLOSEST TO THE NEUTRAL BASELINE - so the reported weights are the
least-distorted weighting consistent with the vote, not a set tuned for effect.
"""
import json, glob, os, collections, io, itertools, math

SRC = os.path.dirname(os.path.abspath(__file__))
games = {g["gameID"]: g for g in json.load(io.open(os.path.join(SRC, "games.json"), encoding="utf-8"))["data"]}
teams = {t["teamID"]: t for t in json.load(io.open(os.path.join(SRC, "teams.json"), encoding="utf-8"))["data"]}
ALLSTAR = {"allstars1", "allstars2"}

# ---- per-game component vectors -------------------------------------------------
# (scores, rec100, thr100, blocks, turnovers, comp100, win)
rows = collections.defaultdict(list)     # pid -> [(week_order, vector)]
name, team_of = {}, {}
tg = collections.defaultdict(collections.Counter)
order = {gid: i for i, gid in enumerate(sorted(games, key=lambda g: games[g]["startTimestamp"]))}

for f in glob.glob(os.path.join(SRC, "pg", "*.json")):
    gid = os.path.basename(f)[:-5]
    g = games[gid]
    if not g["week"]: continue                      # all-star game
    for p in json.load(io.open(f, encoding="utf-8"))["data"]:
        if p["teamID"] in ALLSTAR: continue
        pid = p["player"]["playerID"]
        name[pid] = p["player"]["firstName"].strip() + " " + p["player"]["lastName"].strip()
        tg[pid][p["teamID"]] += 1
        home = p["teamID"] == g["homeTeamID"]
        won = (g["homeScore"] > g["awayScore"]) if home else (g["awayScore"] > g["homeScore"])
        v = (
            (p.get("goals") or 0) + (p.get("assists") or 0) + (p.get("hockeyAssists") or 0),
            (p.get("yardsReceived") or 0) / 100.0,
            (p.get("yardsThrown") or 0) / 100.0,
            (p.get("blocks") or 0),
            (p.get("throwaways") or 0) + (p.get("stalls") or 0) + (p.get("drops") or 0),
            (p.get("completions") or 0) / 100.0,
            1.0 if won else 0.0,
        )
        rows[pid].append((order[gid], g["week"], v))
for pid in tg: team_of[pid] = tg[pid].most_common(1)[0][0]

totals = {pid: tuple(sum(r[2][i] for r in rs) for i in range(7)) for pid, rs in rows.items()}
elig = [pid for pid in rows if len(rows[pid]) >= 8]
print("eligible players:", len(elig))

NAMES = ["scores", "rec_yd/100", "thr_yd/100", "blocks", "turnovers", "completions/100", "team_win"]
BASE  = (1.0, 1.0, 1.0, 1.0, -1.0, 1.0, 1.0)          # neutral baseline
GRID  = {
    0: [1.0],                                          # scores anchors the scale
    1: [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0],
    2: [0.0, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0],
    3: [0.0, 0.5, 1.0, 1.5, 2.0, 3.0],
    4: [-3.0, -2.0, -1.5, -1.0, -0.5, 0.0],
    5: [0.0, 0.5, 1.0, 1.5, 2.0, 3.0],
    6: [0.0, 0.5, 1.0, 2.0, 3.0, 5.0],
}
dist = lambda w: math.sqrt(sum((w[i] - BASE[i]) ** 2 for i in range(7)))

best, hits = None, 0
for w in itertools.product(*[GRID[i] for i in range(7)]):
    dec = sum(w[i] * totals["tdecraene"][i] for i in range(7))
    beaten = False
    for pid in elig:
        if pid == "tdecraene": continue
        if sum(w[i] * totals[pid][i] for i in range(7)) >= dec:
            beaten = True; break
    if beaten: continue
    hits += 1
    d = dist(w)
    if best is None or d < best[0]: best = (d, w)

print("weightings that put Decraene first:", hits)
if not best:
    raise SystemExit("no weighting in this grid ranks Decraene first")

d, W = best
print("chosen (closest to neutral, distance %.2f):" % d)
for i, n in enumerate(NAMES): print("   %-16s %+.2f   (neutral %+.1f)" % (n, W[i], BASE[i]))

sc = {pid: sum(W[i] * totals[pid][i] for i in range(7)) for pid in elig}
board = sorted(elig, key=lambda p: -sc[p])
print("\ntop 10 by fitted MVP score:")
for i, pid in enumerate(board[:10]):
    print("   %2d. %-24s %7.1f%s" % (i + 1, name[pid], sc[pid], "  <<< MVP" if pid == "tdecraene" else ""))

json.dump({"weights": list(W), "names": NAMES, "baseline": list(BASE), "n_solutions": hits,
           "distance": d}, io.open(os.path.join(SRC, "mvp_weights.json"), "w"), indent=1)
