# -*- coding: utf-8 -*-
"""Tests for serve.py.  Run:  python test_serve.py   (pytest also works)

Uses FastAPI's TestClient, so no server and no Modal - but it exercises the same app
object that modal_serve.py deploys, including the auth dependency.

Supabase is stubbed rather than skipped: a fake opener hands the *real* SupabaseStore
rows shaped exactly as PostgREST returns them, built from the local CSV and the
dashboard blob. So the rename, the numeric coercion and the paging are all under test
here too, and the suite still runs offline with nothing provisioned.
"""
import io
import json
import os
import re

os.environ.setdefault("MVP_API_KEY", "test-key-not-the-real-one")

import pandas as pd
from fastapi.testclient import TestClient

import serve

KEY = os.environ["MVP_API_KEY"]
AUTH = {"X-API-Key": KEY}

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECTIONS_CSV = os.path.join(HERE, "ufa_projections_2027.csv")
DASHBOARD_HTML = os.path.join(HERE, "ufa-mvp-race.html")


# ---------------------------------------------------------------- the stub
def _projection_rows():
    """The CSV, renamed back into the Supabase column names push_supabase.py writes."""
    df = pd.read_csv(PROJECTIONS_CSV)
    df.columns = [c.lstrip("﻿") for c in df.columns]      # the file is utf-8-sig
    return [{"id": r["id"], "first_name": r["first"], "last_name": r["last"],
             "abbrev": r["abbr"], "team": r["team"], "division": r["div"],
             "record": r.get("record", ""), "points_played": int(r["pts"]),
             # strings on purpose: PostgREST renders `numeric` columns this way
             "prev_score": str(r["prev"]), "projected_score": str(r["proj"]),
             "chance": str(r["chance"]), "projected_rank": int(r["rank"]),
             "from_season": 2026, "to_season": 2027, "min_points": 100}
            for _, r in df.iterrows()]


def _player_rows():
    """The dashboard's embedded dataset, in the shape of the `players` table."""
    html = io.open(DASHBOARD_HTML, encoding="utf-8").read()
    m = re.search(r'<script type="application/json" id="ufa-data">(.*?)</script>',
                  html, re.S)
    blob = json.loads(m.group(1))
    return [{"id": p["id"], "first_name": p["first"], "last_name": p["last"],
             "jersey": p["num"] or "", "team": p["team"], "abbrev": p["abbr"],
             "division": p["div"], "record": p["record"],
             "games_played": p["gp"], "games_played_full": p["gpFull"],
             "goals": p["g"], "assists": p["a"], "hockey_assists": p["ha"],
             "blocks": p["blocks"], "completions": p["cmp"],
             "throw_attempts": p["att"], "turnovers": p["to"],
             "yards_thrown": p["ty"], "yards_received": p["ry"],
             "plus_minus": p["pm"], "total_scores": p["sc"],
             "mvp_score": str(p["mvp"]), "ranks": p["rank"], "trend": p["trend"],
             "headshot": p.get("img")}
            for p in blob["players"]]


TABLES = {"projections": _projection_rows(), "players": _player_rows()}


class FakeResponse(object):
    def __init__(self, rows):
        self._body = json.dumps(rows).encode("utf-8")

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Down(Exception):
    """Stands in for whatever Supabase does when it is unreachable."""


def fake_opener(req, timeout=None):
    if getattr(fake_opener, "broken", False):
        raise Down("connection refused")
    table = req.full_url.split("/rest/v1/")[1].split("?")[0]
    rows = TABLES[table]
    start, end = (int(x) for x in req.get_header("Range").split("-"))
    return FakeResponse(rows[start:end + 1])


def build_store():
    store = serve.SupabaseStore(url="https://stub.supabase.co", key="stub-key",
                             cache_seconds=0)
    store._open = fake_opener
    return store


serve.set_store(build_store())
client = TestClient(serve.create_app())

DECRAENE_2026 = {
    "goals": 40, "assists": 69, "hockey_assists": 36, "blocks": 6,
    "completions": 430, "throw_attempts": 455, "throwaways": 22, "drops": 6,
    "catches": 380, "yards_thrown": 3364, "yards_received": 3657,
    "o_points_played": 246, "d_points_played": 5, "seconds_played": 14000,
    "team_wins": 11, "team_losses": 3,
}


# ---------------------------------------------------------------- auth
def test_health_needs_no_key():
    r = client.get("/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok" and body["model_loaded"] is True
    assert body["target_season"] == body["trained_through"] + 1


def test_root_returns_a_usable_index():
    """The bare URL is the first thing anyone pastes into Postman or a browser.

    FastAPI's default there is a 22-byte {"detail":"Not Found"}, which reads as a
    broken service rather than as "you want /docs".
    """
    r = client.get("/")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["service"] and body["docs"] == "/docs"
    paths = {e["path"] for e in body["routes"]}
    assert {"/health", "/players", "/season/players", "/predict", "/compare"} <= paths
    # it must say which routes need the key, and be honest about which do not
    need = {e["path"] for e in body["routes"] if e["auth"]}
    assert "/players" in need and "/health" not in need


def test_root_works_without_a_key_or_a_database():
    """It exists to diagnose the other two being down, so it must not need either."""
    fake_opener.broken = True
    serve.set_store(build_store())
    old = os.environ["MVP_API_KEY"]
    os.environ["MVP_API_KEY"] = ""
    try:
        r = client.get("/")
        assert r.status_code == 200, r.text
        assert r.json()["docs"] == "/docs"
    finally:
        os.environ["MVP_API_KEY"] = old
        fake_opener.broken = False
        serve.set_store(build_store())


def test_protected_routes_reject_a_missing_key():
    for method, path, kw in [("get", "/players", {}),
                             ("get", "/players/tdecraene", {}),
                             ("get", "/season/players", {}),
                             ("get", "/season/players/tdecraene", {}),
                             ("post", "/predict", {"json": DECRAENE_2026}),
                             ("post", "/compare", {"json": {"picks": ["khenke", "aatkins"]}})]:
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 401, "%s %s allowed an unauthenticated call" % (method, path)


def test_every_route_but_health_demands_a_key():
    """The guard that outlives this file.

    Walks the live OpenAPI spec instead of a hand-written list, so an endpoint added
    outside the authenticated router fails here rather than shipping open to the world.
    """
    spec = client.get("/openapi.json").json()
    # Every addition here is a deliberate decision to expose something. Both of
    # these are metadata only: no player rows, no model call, no Supabase read on
    # "/" at all. Anything that touches data belongs on the authenticated router.
    public = {"/health", "/"}
    unguarded = []
    for path, methods in spec["paths"].items():
        if path in public:
            continue
        for method, op in methods.items():
            if method not in ("get", "post", "put", "patch", "delete"):
                continue
            if not op.get("security"):
                unguarded.append("%s %s" % (method.upper(), path))
    assert not unguarded, "these routes have no API key requirement: %s" % unguarded


def test_wrong_key_is_rejected():
    r = client.get("/players", headers={"X-API-Key": KEY + "x"})
    assert r.status_code == 401
    r = client.get("/players", headers={"X-API-Key": ""})
    assert r.status_code == 401
    assert client.get("/players", headers={"X-API-Key": KEY[:-1]}).status_code == 401


def test_a_server_with_no_key_configured_refuses_rather_than_opens():
    """An empty MVP_API_KEY must never mean 'no key needed'."""
    os.environ["MVP_API_KEY"] = ""
    try:
        r = client.get("/players", headers=AUTH)
        assert r.status_code == 503, r.text
        assert client.get("/players").status_code == 503
    finally:
        os.environ["MVP_API_KEY"] = KEY


def test_right_key_is_accepted():
    assert client.get("/players", headers=AUTH).status_code == 200


def test_key_is_a_declared_openapi_scheme():
    """The Authorize button in /docs only appears if the scheme is in the schema."""
    spec = client.get("/openapi.json").json()
    schemes = spec["components"]["securitySchemes"]
    assert any(s.get("in") == "header" and s.get("name") == "X-API-Key"
               for s in schemes.values()), schemes
    assert "/predict" in spec["paths"] and "/compare" in spec["paths"]
    assert spec["paths"]["/health"]["get"].get("security") in (None, [])


def test_docs_render():
    assert client.get("/docs").status_code == 200


# ---------------------------------------------------------------- supabase
def test_health_reports_supabase_as_the_source():
    body = client.get("/health").json()
    assert body["source"] == "supabase"
    assert body["players"] == len(TABLES["projections"])
    assert body["season_players"] == len(TABLES["players"])
    assert body["projections_season"] == 2027
    assert body["stale"] is False


def test_rows_really_come_from_the_store_not_a_file():
    """Change the stub's table, and the API changes with it."""
    TABLES["projections"].append(dict(TABLES["projections"][0], id="ghost",
                                      first_name="Ghost", last_name="Player"))
    try:
        serve.set_store(build_store())
        assert client.get("/players/ghost", headers=AUTH).status_code == 200
    finally:
        TABLES["projections"].pop()
        serve.set_store(build_store())


def test_data_routes_503_when_supabase_is_down():
    fake_opener.broken = True
    serve.set_store(build_store())
    try:
        for method, path, kw in [("get", "/players", {}),
                                 ("get", "/players/tdecraene", {}),
                                 ("get", "/season/players", {}),
                                 ("get", "/season/players/tdecraene", {})]:
            r = getattr(client, method)(path, headers=AUTH, **kw)
            assert r.status_code == 503, "%s %s returned %d" % (method, path, r.status_code)
        assert client.get("/health").status_code == 503
    finally:
        fake_opener.broken = False
        serve.set_store(build_store())


def test_predict_still_works_when_supabase_is_down():
    """/predict is pure model - it has no reason to need the database."""
    fake_opener.broken = True
    serve.set_store(build_store())
    try:
        r = client.post("/predict", json=DECRAENE_2026, headers=AUTH)
        assert r.status_code == 200, r.text
    finally:
        fake_opener.broken = False
        serve.set_store(build_store())


def test_a_down_database_does_not_leak_the_key():
    fake_opener.broken = True
    serve.set_store(build_store())
    try:
        assert "stub-key" not in client.get("/players", headers=AUTH).text
    finally:
        fake_opener.broken = False
        serve.set_store(build_store())


# ---------------------------------------------------------------- predictions
def test_predict_matches_the_exported_projection():
    """The API must agree with the stored projections for the same stat line."""
    seasons = pd.read_csv("ufa_player_season_2021_2026.csv")
    row = seasons[(seasons.year == 2026) & (seasons.player_id == "tdecraene")].iloc[0]
    payload = {c: float(row[c]) for c in serve.FEATURE_TOTALS}

    r = client.post("/predict", json=payload, headers=AUTH)
    assert r.status_code == 200, r.text
    got = r.json()

    stored = client.get("/players/tdecraene", headers=AUTH).json()
    assert abs(got["projected_score"] - stored["projected_score"]) < 0.05
    assert abs(got["chance_top5"] - stored["chance_top5"]) < 0.005
    assert abs(got["current_score"] - stored["current_score"]) < 0.05
    assert 0 <= got["chance_top5"] <= 1


def test_predict_accepts_a_sparse_line():
    r = client.post("/predict", json={"goals": 10}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["projected_score"] >= 0


def test_a_better_season_projects_higher():
    weak = client.post("/predict", json={"goals": 5, "assists": 5, "o_points_played": 100,
                                         "yards_received": 400}, headers=AUTH).json()
    strong = client.post("/predict", json=DECRAENE_2026, headers=AUTH).json()
    assert strong["projected_score"] > weak["projected_score"]
    assert strong["chance_top5"] > weak["chance_top5"]


def test_player_lookup_and_404():
    r = client.get("/players/tdecraene", headers=AUTH)
    assert r.status_code == 200 and r.json()["last"] == "Decraene"
    assert client.get("/players/not-a-real-player", headers=AUTH).status_code == 404


def test_player_search_and_limit():
    r = client.get("/players", params={"search": "BOS", "limit": 5}, headers=AUTH)
    assert r.status_code == 200
    rows = r.json()
    assert 0 < len(rows) <= 5
    assert all(p["abbr"] == "BOS" for p in rows)


def test_a_mononym_player_serialises():
    """The league has a player with no first name. A blank cell is data, not an error."""
    rows = client.get("/players", params={"limit": 1000}, headers=AUTH).json()
    assert len(rows) == len(TABLES["projections"]), "the full roster did not serialise"
    mononyms = [p for p in rows if not p["first"]]
    assert mononyms and mononyms[0]["last"]


def test_search_does_not_match_the_string_nan():
    """A blank name rendered as "nan" would make a search for 'an' return it."""
    rows = client.get("/players", params={"search": "nan", "limit": 1000},
                      headers=AUTH).json()
    assert all("nan" in (p["first"] + p["last"] + p["team"] + p["abbr"]).lower()
               for p in rows)


def test_players_come_back_in_rank_order():
    rows = client.get("/players", params={"limit": 20}, headers=AUTH).json()
    ranks = [p["projected_rank"] for p in rows]
    assert ranks == sorted(ranks) and ranks[0] == 1


# ---------------------------------------------------------------- season
def test_season_players_are_sorted_by_mvp_score():
    r = client.get("/season/players", params={"limit": 10}, headers=AUTH)
    assert r.status_code == 200, r.text
    scores = [p["mvp_score"] for p in r.json()]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] > 0


def test_season_player_lookup_and_404():
    r = client.get("/season/players/tdecraene", headers=AUTH)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["last"] == "Decraene" and p["goals"] > 0
    assert isinstance(p["ranks"], dict) and p["ranks"].get("mvp") == 1
    assert client.get("/season/players/nobody", headers=AUTH).status_code == 404


def test_season_player_numbers_are_numbers_not_strings():
    """mvp_score arrives from PostgREST as a string; it must not sort lexically."""
    p = client.get("/season/players/tdecraene", headers=AUTH).json()
    assert isinstance(p["mvp_score"], float) and isinstance(p["goals"], int)


def test_season_search_filters():
    rows = client.get("/season/players", params={"search": "BOS"}, headers=AUTH).json()
    assert rows and all(p["abbr"] == "BOS" for p in rows)


def test_season_and_projection_routes_are_separate_tables():
    season = client.get("/season/players", params={"limit": 1000}, headers=AUTH).json()
    proj = client.get("/players", params={"limit": 1000}, headers=AUTH).json()
    assert len(season) != len(proj), "the two routes are reading the same table"


# ---------------------------------------------------------------- compare
def test_compare_ranks_and_names_a_winner():
    picks = ["khenke", "tdecraene", "aatkins", "jfairfax", "cyorgason"]
    r = client.post("/compare", json={"picks": picks}, headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    scores = [e["projected_score"] for e in body["ranked"]]
    assert scores == sorted(scores, reverse=True), "not ranked"
    assert [e["rank"] for e in body["ranked"]] == [1, 2, 3, 4, 5]
    assert body["winner"] == body["ranked"][0]["name"]
    assert abs(body["margin"] - (scores[0] - scores[1])) < 0.02
    assert body["within_noise"] is (body["margin"] < body["typical_error"])


def test_compare_accepts_custom_stat_lines():
    r = client.post("/compare", json={"picks": [
        "tdecraene",
        {"name": "Invented Superstar", "stats": DECRAENE_2026},
    ]}, headers=AUTH)
    assert r.status_code == 200, r.text
    names = [e["name"] for e in r.json()["ranked"]]
    assert "Invented Superstar" in names


def test_compare_enforces_two_to_five():
    assert client.post("/compare", json={"picks": ["khenke"]},
                       headers=AUTH).status_code == 422
    assert client.post("/compare", json={"picks": ["khenke"] * 6},
                       headers=AUTH).status_code == 422


def test_compare_rejects_an_empty_pick():
    r = client.post("/compare", json={"picks": ["khenke", {"name": "nothing here"}]},
                    headers=AUTH)
    assert r.status_code == 422


if __name__ == "__main__":
    fails = 0
    for n, f in sorted(globals().items()):
        if n.startswith("test_"):
            try:
                f()
                print("PASS", n)
            except Exception as e:
                fails += 1
                print("FAIL", n, "->", type(e).__name__, e)
    raise SystemExit(fails)
