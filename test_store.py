# -*- coding: utf-8 -*-
"""Tests for the Supabase reader in serve.py.  Run:  python test_store.py

No network: every test swaps in a fake opener, so these check the request we
*build* and how we react to what comes back - the two things that actually break.
"""
import json

import serve as ss


# ---------------------------------------------------------------- fakes
class FakeResponse(object):
    def __init__(self, body, status=200, headers=None):
        self._body = body.encode("utf-8") if isinstance(body, str) else body
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Recorder(object):
    """Stands in for urlopen. Records requests, replays queued responses."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, req, timeout=None):
        self.requests.append(req)
        if not self.responses:
            raise AssertionError("unexpected extra request to " + req.full_url)
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    @property
    def urls(self):
        return [r.full_url for r in self.requests]


def store(opener, **kw):
    kw.setdefault("url", "https://proj.supabase.co")
    kw.setdefault("key", "anon-key")
    kw.setdefault("cache_seconds", 0)
    s = ss.SupabaseStore(**kw)
    s._open = opener
    return s


def page(rows, first=0, total=None):
    """A PostgREST response with a Content-Range header."""
    last = first + len(rows) - 1 if rows else 0
    total = len(rows) if total is None else total
    return FakeResponse(json.dumps(rows),
                        headers={"Content-Range": "%d-%d/%d" % (first, last, total)})


ROW = {"id": "khenke", "first_name": "Kyle", "last_name": "Henke", "abbrev": "ATX",
       "team": "Austin Sol", "division": "South", "record": "12-2",
       "points_played": 305, "prev_score": 146.1, "projected_score": 112.3,
       "chance": 0.3069, "projected_rank": 1, "from_season": 2026,
       "to_season": 2027, "min_points": 100}


# ---------------------------------------------------------------- requests
def test_builds_a_postgrest_url_with_credentials():
    rec = Recorder(page([ROW]))
    store(rec).select("projections")
    req = rec.requests[0]
    assert req.full_url.startswith("https://proj.supabase.co/rest/v1/projections")
    assert req.get_header("Apikey") == "anon-key"
    assert req.get_header("Authorization") == "Bearer anon-key"


def test_passes_select_and_order_through():
    rec = Recorder(page([ROW]))
    store(rec).select("projections", columns="id,team", order="projected_rank.asc")
    url = rec.urls[0]
    assert "select=id%2Cteam" in url or "select=id,team" in url
    assert "order=projected_rank.asc" in url


def test_pages_past_the_postgrest_row_cap():
    """1000 rows is PostgREST's default ceiling - a 1200-row table must not silently
    come back truncated."""
    big = [dict(ROW, id="p%d" % i) for i in range(1000)]
    rest = [dict(ROW, id="p%d" % i) for i in range(1000, 1200)]
    rec = Recorder(page(big, first=0, total=1200), page(rest, first=1000, total=1200))
    rows = store(rec).select("projections")
    assert len(rows) == 1200
    assert rows[-1]["id"] == "p1199"
    assert rec.requests[0].get_header("Range") == "0-999"
    assert rec.requests[1].get_header("Range") == "1000-1999"


def test_a_short_page_ends_the_walk():
    rec = Recorder(page([ROW], first=0, total=1))
    assert len(store(rec).select("projections")) == 1
    assert len(rec.requests) == 1


# ---------------------------------------------------------------- failure
def test_network_error_becomes_supabase_error():
    rec = Recorder(IOError("connection refused"))
    try:
        store(rec).select("projections")
    except ss.SupabaseError as e:
        assert "projections" in str(e)
    else:
        raise AssertionError("a dead connection did not raise SupabaseError")


def test_http_error_becomes_supabase_error():
    import urllib.error
    err = urllib.error.HTTPError("u", 401, "Unauthorized", {}, None)
    err.read = lambda: b'{"message":"invalid api key"}'
    rec = Recorder(err)
    try:
        store(rec).select("projections")
    except ss.SupabaseError as e:
        assert "401" in str(e)
    else:
        raise AssertionError("a 401 did not raise SupabaseError")


def test_missing_credentials_raise_before_any_request():
    rec = Recorder()                      # any request at all would assert
    s = store(rec, url="", key="")
    try:
        s.select("projections")
    except ss.SupabaseError as e:
        assert "SUPABASE_URL" in str(e)
    else:
        raise AssertionError("an unconfigured store did not raise")


def test_configured_reports_missing_credentials():
    assert store(Recorder()).configured is True
    assert store(Recorder(), key="").configured is False


# ---------------------------------------------------------------- cache
def test_cache_serves_a_second_read_without_a_request():
    rec = Recorder(page([ROW]))           # exactly one response queued
    s = store(rec, cache_seconds=300)
    assert s.select("projections")[0]["id"] == "khenke"
    assert s.select("projections")[0]["id"] == "khenke"
    assert len(rec.requests) == 1, "second read hit the network"


def test_cache_expires():
    rec = Recorder(page([ROW]), page([dict(ROW, id="later")]))
    s = store(rec, cache_seconds=300)
    now = [1000.0]
    s._now = lambda: now[0]
    assert s.select("projections")[0]["id"] == "khenke"
    now[0] += 301
    assert s.select("projections")[0]["id"] == "later"


def test_different_queries_cache_separately():
    rec = Recorder(page([ROW]), page([dict(ROW, id="other")]))
    s = store(rec, cache_seconds=300)
    s.select("projections")
    s.select("players")
    assert len(rec.requests) == 2


def test_cache_can_be_cleared():
    rec = Recorder(page([ROW]), page([ROW]))
    s = store(rec, cache_seconds=300)
    s.select("projections")
    s.clear_cache()
    s.select("projections")
    assert len(rec.requests) == 2


# ---------------------------------------------------------------- frames
def test_projections_frame_uses_the_short_column_names():
    """The endpoints index these by the CSV's short names - the rename is the contract."""
    rec = Recorder(page([ROW]))
    df = store(rec).projections()
    for c in ["id", "first", "last", "abbr", "team", "div", "pts", "prev",
              "proj", "chance", "rank"]:
        assert c in df.columns, "missing %r in %s" % (c, list(df.columns))
    r = df.loc["khenke"]
    assert r["first"] == "Kyle" and r["rank"] == 1 and abs(r["proj"] - 112.3) < 1e-9
    assert df.index.name == "id"


def test_projections_frame_is_numeric():
    """Supabase returns numerics as strings often enough to matter."""
    rec = Recorder(page([dict(ROW, projected_score="112.3", projected_rank="1")]))
    df = store(rec).projections()
    assert float(df.loc["khenke", "proj"]) == 112.3
    assert int(df.loc["khenke", "rank"]) == 1


def test_projections_seasons_come_from_the_rows():
    rec = Recorder(page([ROW]))
    s = store(rec)
    assert s.projection_seasons() == (2026, 2027)


def test_empty_projections_table_is_an_error_not_an_empty_page():
    """A cleared table means push_supabase.py has not run - say so, don't serve zero
    players as if that were the answer."""
    rec = Recorder(page([], total=0))
    try:
        store(rec).projections()
    except ss.SupabaseError as e:
        assert "projections" in str(e)
    else:
        raise AssertionError("an empty table did not raise")


def test_players_frame_shape():
    prow = {"id": "khenke", "first_name": "Kyle", "last_name": "Henke", "jersey": "7",
            "team": "Austin Sol", "abbrev": "ATX", "division": "South", "record": "12-2",
            "games_played": 14, "games_played_full": 12, "goals": 30, "assists": 40,
            "hockey_assists": 20, "blocks": 5, "completions": 300, "throw_attempts": 320,
            "turnovers": 20, "yards_thrown": 2000, "yards_received": 1500,
            "plus_minus": 55, "total_scores": 70, "mvp_score": 146.1,
            "ranks": {"mvp": 1}, "trend": {"mvp": 2}, "headshot": "http://x/y.png"}
    rec = Recorder(page([prow]))
    df = store(rec).players()
    r = df.loc["khenke"]
    assert r["first"] == "Kyle" and r["jersey"] == "7"
    assert int(r["goals"]) == 30 and abs(float(r["mvp_score"]) - 146.1) < 1e-9
    assert r["ranks"] == {"mvp": 1}
    assert r["headshot"] == "http://x/y.png"


def test_players_tolerates_a_null_headshot():
    rec = Recorder(page([{"id": "x", "first_name": "A", "last_name": "B", "jersey": "",
                          "team": "T", "abbrev": "T", "division": "South", "record": "",
                          "mvp_score": 1, "ranks": {}, "trend": {}, "headshot": None}]))
    df = store(rec).players()
    assert df.loc["x", "headshot"] is None


# ---------------------------------------------------------------- env wiring
def test_from_env_reads_the_standard_names():
    import os
    old = {k: os.environ.get(k) for k in
           ("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY", "MVP_CACHE_SECONDS")}
    try:
        os.environ["SUPABASE_URL"] = "https://x.supabase.co"
        os.environ.pop("SUPABASE_KEY", None)
        os.environ["SUPABASE_ANON_KEY"] = "anon"
        os.environ["MVP_CACHE_SECONDS"] = "42"
        s = ss.SupabaseStore.from_env()
        assert s.url == "https://x.supabase.co"
        assert s.key == "anon"          # falls back to the anon name
        assert s.cache_seconds == 42
        os.environ["SUPABASE_KEY"] = "explicit"
        assert ss.SupabaseStore.from_env().key == "explicit"   # and prefers the plain one
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_trailing_slash_in_the_url_does_not_double_up():
    rec = Recorder(page([ROW]))
    store(rec, url="https://proj.supabase.co/").select("projections")
    assert "//rest" not in rec.urls[0].replace("https://", "")


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
