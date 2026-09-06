# -*- coding: utf-8 -*-
import json, glob, collections, os
games = {g["gameID"]: g for g in json.load(open("games.json"))["data"]}
REG = {"week-%d" % i for i in range(1, 14)}
tot = collections.defaultdict(collections.Counter)
for f in glob.glob("pg/*.json"):
    g = games[os.path.basename(f)[:-5]]
    if g["week"] not in REG: continue
    for p in json.load(open(f, encoding="utf-8"))["data"]:
        c = tot[p["player"]["playerID"]]
        c["games"] += 1
        for k, v in p.items():
            if isinstance(v, (int, float)): c[k] += v
val = {
 "pm":     lambda c: c["goals"]+c["assists"]+c["blocks"]-c["throwaways"]-c["stalls"]-c["drops"],
 "sc":     lambda c: c["goals"]+c["assists"],
 "blocks": lambda c: c["blocks"],
 "ry":     lambda c: c["yardsReceived"],
 "ty":     lambda c: c["yardsThrown"],
}
pool = [p for p, c in tot.items() if c["games"] >= 1]
order = {k: sorted(pool, key=lambda p: -fn(tot[p])) for k, fn in val.items()}
rank = {}
for k, lst in order.items():
    seen = {}
    for i, p in enumerate(lst):
        v = val[k](tot[p]); seen.setdefault(v, i + 1)
        rank.setdefault(p, {})[k] = seen[v]

d = json.load(open("ufa2025.json", encoding="utf-8"))
for p in d["players"]:
    p["rank"] = rank[p["id"]]
d["leagueN"] = len(pool)
d["games"] = sum(1 for g in games.values() if g["week"] in REG)
json.dump(d, open("ufa2025.json", "w", encoding="utf-8"), ensure_ascii=False, separators=(",", ":"))
print("players in league:", len(pool), "| reg-season games:", d["games"])
print("Decraene ranks:", rank["tdecraene"], "| De Marree ranks:", rank["ddemarre"])
print("size", os.path.getsize("ufa2025.json"))
