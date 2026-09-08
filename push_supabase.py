# -*- coding: utf-8 -*-
"""Load the dashboard dataset into Supabase.

Source is ufa2025.json when build2.py has been run; otherwise the JSON blob
embedded in ufa-mvp-race.html, so the tables can be seeded from a fresh clone
without re-downloading the season.

Writes with the service_role key, which bypasses row-level security, and upserts
so re-running is safe.

    python push_supabase.py        ->  season_meta, metrics, players, awards
"""
import io, os, re, json, urllib.request, urllib.error

import env

HERE = os.path.dirname(os.path.abspath(__file__))
JSON_SRC = os.path.join(HERE, "ufa2025.json")
HTML_SRC = os.path.join(HERE, "ufa-mvp-race.html")
SEASON = 2025


def dataset():
    if os.path.exists(JSON_SRC):
        print("source: ufa2025.json")
        return json.load(io.open(JSON_SRC, encoding="utf-8"))
    html = io.open(HTML_SRC, encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="ufa-data">(.*?)</script>', html, re.S)
    if not m:
        raise SystemExit("no dataset found - run build2.py first")
    print("source: embedded blob in ufa-mvp-race.html")
    return json.loads(m.group(1))


def post(url, key, table, rows, on_conflict):
    """Upsert rows into one table via PostgREST."""
    body = json.dumps(rows, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "%s/rest/v1/%s?on_conflict=%s" % (url.rstrip("/"), table, on_conflict),
        data=body, method="POST",
        headers={"apikey": key, "Authorization": "Bearer " + key,
                 "Content-Type": "application/json",
                 "Prefer": "resolution=merge-duplicates,return=minimal"})
    try:
        urllib.request.urlopen(req).read()
    except urllib.error.HTTPError as e:
        raise SystemExit("%s: %s %s\n%s" % (table, e.code, e.reason, e.read().decode("utf-8", "replace")))
    print("%-14s %4d rows" % (table, len(rows)))


def main():
    cfg = env.load("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
    url, key = cfg["SUPABASE_URL"], cfg["SUPABASE_SERVICE_ROLE_KEY"]
    d = dataset()

    post(url, key, "season_meta", [{
        "season": SEASON, "weeks": d["weeks"], "reg_weeks": d["regWeeks"],
        "playoff_from": d["playoffFrom"], "league_n": d["leagueN"],
        "games": d["games"], "games_full": d["gamesFull"], "fit": d["fit"],
    }], "season")

    post(url, key, "metrics", [{
        "k": m["k"], "sort_order": i, "label": m["label"], "short": m["short"],
        "descr": m["desc"], "ceil": m["ceil"], "weeks": m["weeks"],
        "season_scope": m["season"],
    } for i, m in enumerate(d["metrics"])], "k")

    post(url, key, "players", [{
        "id": p["id"], "first_name": p["first"], "last_name": p["last"],
        "jersey": p["num"] or "", "team": p["team"], "abbrev": p["abbr"],
        "division": p["div"], "record": p["record"],
        "games_played": p["gp"], "games_played_full": p["gpFull"],
        "goals": p["g"], "assists": p["a"], "hockey_assists": p["ha"],
        "blocks": p["blocks"], "completions": p["cmp"], "throw_attempts": p["att"],
        "turnovers": p["to"], "yards_thrown": p["ty"], "yards_received": p["ry"],
        "plus_minus": p["pm"], "total_scores": p["sc"], "mvp_score": p["mvp"],
        "ranks": p["rank"], "trend": p["trend"], "headshot": p.get("img"),
    } for p in d["players"]], "id")

    # awards reference players, so they go last
    post(url, key, "awards", [{
        "award": a["a"], "sort_order": i, "player_id": a["p"], "note": a.get("note", ""),
    } for i, a in enumerate(d["awards"])], "award")

    print("\nloaded season %d into %s" % (SEASON, url))


if __name__ == "__main__":
    main()
