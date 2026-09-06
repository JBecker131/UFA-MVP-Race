import json, urllib.request, os, time, collections
B = "https://www.backend.ufastats.com/api/v1/"
def get(p):
    for _ in range(3):
        try:
            with urllib.request.urlopen(B+p, timeout=45) as r: return json.load(r)
        except Exception as e:
            print("retry", p, e); time.sleep(2)
    raise SystemExit("failed "+p)

games = json.load(open("games.json"))["data"]
print(collections.Counter(g["week"] for g in games))
os.makedirs("pg", exist_ok=True)
for i, g in enumerate(games):
    f = "pg/%s.json" % g["gameID"]
    if os.path.exists(f): continue
    json.dump(get("playerGameStats?gameID="+g["gameID"]), open(f,"w"))
    if i % 20 == 0: print(i, g["gameID"], flush=True)
print("done", len(os.listdir("pg")))
