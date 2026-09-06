# -*- coding: utf-8 -*-
import json, glob, collections, urllib.request, io, base64, os
from PIL import Image

games = {g["gameID"]: g for g in json.load(open("games.json"))["data"]}
teams = {t["teamID"]: t for t in json.load(open("teams.json"))["data"]}
REG = {"week-%d" % i for i in range(1, 14)}
WEEKS = list(range(1, 14))

lines = collections.defaultdict(dict)   # pid -> week -> summed statline
meta = {}
for f in glob.glob("pg/*.json"):
    gid = os.path.basename(f)[:-5]
    g = games[gid]
    if g["week"] not in REG: continue
    wk = int(g["week"].split("-")[1])
    for p in json.load(open(f, encoding="utf-8"))["data"]:
        pid = p["player"]["playerID"]
        meta[pid] = (p["player"]["firstName"].strip(), p["player"]["lastName"].strip(),
                     p["teamID"], (p.get("jerseyNumber") or "").strip())
        d = lines[pid].setdefault(wk, collections.Counter())
        d["games"] += 1
        for k, v in p.items():
            if isinstance(v, (int, float)): d[k] += v

def pm(c):  return c["goals"] + c["assists"] + c["blocks"] - c["throwaways"] - c["stalls"] - c["drops"]
def sc(c):  return c["goals"] + c["assists"]

METRICS = [
    {"k":"pm",    "label":"Plus / minus",    "short":"+/-",     "desc":"Goals + assists + blocks, minus throwaways, drops and stalls \u2014 the UFA's headline composite.", "fn":pm,                  "ceil":100,   "fmt":"int"},
    {"k":"sc",    "label":"Total scores",    "short":"G+A",     "desc":"Goals plus assists: every point the player finished or set up.",                                    "fn":sc,                  "ceil":100,   "fmt":"int"},
    {"k":"blocks","label":"Blocks",          "short":"Blocks",  "desc":"Defensive blocks \u2014 the currency of the Defensive Player of the Year ballot.",                    "fn":lambda c:c["blocks"],"ceil":30,    "fmt":"int"},
    {"k":"ry",    "label":"Receiving yards", "short":"Rec yd",  "desc":"Yards gained catching the disc over the regular season.",                                            "fn":lambda c:c["yardsReceived"],"ceil":4000,"fmt":"int"},
]

totals, weeklyCum = {}, {}
for pid, wks in lines.items():
    agg = collections.Counter()
    for w in wks.values(): agg.update(w)
    totals[pid] = agg
    weeklyCum[pid] = {}
    for m in METRICS:
        run, out = 0, []
        for w in WEEKS:
            if w in wks: run += m["fn"](wks[w])
            out.append(round(run, 1))
        weeklyCum[pid][m["k"]] = out

# candidate pool: union of the top 10 in every metric
pool = []
for m in METRICS:
    for pid in sorted(totals, key=lambda p: -m["fn"](totals[p]))[:10]:
        if pid not in pool: pool.append(pid)
for pid in ("tdecraene","ddemarre","tjohnson1"):
    if pid not in pool: pool.append(pid)
print("pool size", len(pool))

# real headshots from watchufa.com, downscaled and embedded
CAND = ["https://watchufa.com/sites/default/files/players/profile-images/%s_profile.jpg",
        "https://watchufa.com/sites/default/files/players/profile-images/%s_profile.png",
        "https://watchufa.com/sites/default/files/styles/medium/public/players/profile-images/%s_profile.jpg"]
def headshot(pid):
    for t in CAND:
        try:
            req = urllib.request.Request(t % pid, headers={"User-Agent":"Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.status != 200: continue
                raw = r.read()
            im = Image.open(io.BytesIO(raw)).convert("RGB")
            w, h = im.size; s = min(w, h)
            im = im.crop(((w-s)//2, 0, (w-s)//2+s, s)).resize((168,168), Image.LANCZOS)
            buf = io.BytesIO(); im.save(buf, "JPEG", quality=80, optimize=True)
            return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()
        except Exception:
            continue
    return None

out = []
for pid in pool:
    f, l, t, j = meta[pid]
    tm = teams[t]; c = totals[pid]
    img = headshot(pid)
    print(("+" if img else "-"), pid, f, l)
    out.append({
        "id": pid, "first": f, "last": l, "num": j,
        "team": tm["fullName"], "abbr": tm["abbrev"], "div": tm["division"]["name"],
        "record": "%d-%d" % (tm["wins"], tm["losses"]),
        "gp": c["games"], "g": c["goals"], "a": c["assists"], "ha": c["hockeyAssists"],
        "blocks": c["blocks"], "cmp": c["completions"], "att": c["throwAttempts"],
        "to": c["throwaways"] + c["stalls"] + c["drops"],
        "ty": c["yardsThrown"], "ry": c["yardsReceived"],
        "hucks": c["hucksCompleted"], "huckAtt": c["hucksAttempted"],
        "pm": pm(c), "sc": sc(c),
        "trend": weeklyCum[pid], "img": img,
    })

json.dump({
    "weeks": WEEKS,
    "metrics": [{k: m[k] for k in ("k","label","short","desc","ceil","fmt")} for m in METRICS],
    "players": out,
    "awards": [
        {"a":"Most Valuable Player",       "p":"tdecraene", "note":"Led the UFA in total scores and receiving yards; youngest and first internationally born MVP."},
        {"a":"Defensive Player of the Year","p":"tjohnson1","note":"Boston's second award winner of the night."},
        {"a":"Rookie of the Year",         "p":"ddemarre",  "note":"Unanimous selection \u2014 and the regular season's plus/minus leader."},
    ],
}, open("ufa2025.json","w",encoding="utf-8"), ensure_ascii=False)
print("wrote ufa2025.json", os.path.getsize("ufa2025.json"))
