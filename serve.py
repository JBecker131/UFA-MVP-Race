# -*- coding: utf-8 -*-
"""The UFA MVP projection service: FastAPI app plus its Supabase reader.

Three files ship in the Modal image - this one, pipeline_def.py, and
pipeline.joblib - so the Supabase client lives here rather than in a module of
its own. It is the second section below, and nothing above it depends on FastAPI.

No Modal import anywhere in this file: it runs under uvicorn, under TestClient,
and inside a container without changing. modal_serve.py is the deployment wrapper,
and it imports this module inside the Modal function rather than at module scope.

    set MVP_API_KEY=... && uvicorn serve:app --reload
    open http://127.0.0.1:8000/docs

Data comes from Supabase, not from a file: the projections and current-season
tables are read over PostgREST at request time and cached for a few minutes, so
loading new numbers is `python push_supabase.py` with no redeploy. When Supabase
is unreachable the data routes return 503 rather than serving a stale copy. The
model bundle is the one thing read from disk, because it is a build artifact
rather than data.

Every endpoint except /health and the docs requires an X-API-Key header, enforced
on the router that carries them so a new route cannot be added without it.
"""
import json
import os
import secrets as _secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Union

import joblib
import pandas as pd
from fastapi import (APIRouter, Depends, FastAPI, HTTPException, Query, Request,
                     Security, status)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field

from pipeline_def import FEATURE_TOTALS, TOP_N, mvp_score

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_PATH = os.environ.get("MVP_MODEL_PATH", os.path.join(HERE, "pipeline.joblib"))
# comma-separated list, or "*" - only matters if a browser calls this directly
CORS_ORIGINS = [o.strip() for o in os.environ.get("MVP_CORS_ORIGINS", "").split(",") if o.strip()]

API_KEY_NAME = "X-API-Key"
_api_key_header = APIKeyHeader(name=API_KEY_NAME, auto_error=False)


def _load_dotenv():
    """Fold .env into the environment for local runs, without overriding it.

    On Modal the values arrive as Secrets and there is no .env, so this is a no-op
    there. Real environment variables always win, which is what makes the same
    file work under uvicorn, under the tests, and in a container.
    """
    try:
        import env
        for k, v in env.load("SUPABASE_URL", "SUPABASE_KEY", "SUPABASE_ANON_KEY",
                             "MVP_API_KEY", required=False).items():
            if v and not os.environ.get(k):
                os.environ[k] = v
    except Exception:                                            # noqa: BLE001
        pass                    # no .env, or no env.py - the environment is enough


_load_dotenv()


# ==================================================================== supabase
# PostgREST over urllib, deliberately: push_supabase.py already talks to the same
# API this way, and the supabase-py client would add a dependency (plus its
# transitive httpx tree) to an image whose cold-start time is what we pay for.
# What we need is two GETs.
#
# Credentials come from SUPABASE_URL plus SUPABASE_KEY (or SUPABASE_ANON_KEY).
# The anon key is the right one: schema.sql grants anon a read-only SELECT policy
# on these tables and this code never writes. Never the service_role key.

PAGE = 1000             # PostgREST's own default ceiling; asking for more is ignored
TIMEOUT = 15            # seconds per request
CACHE_SECONDS = 300


class SupabaseError(RuntimeError):
    """Supabase could not be read. The app maps this to a 503."""


# Supabase column -> the short name the endpoints and ufa_projections_*.csv use.
# Keeping the API's field names stable across the move off the CSV is the point of
# the mapping: the dashboard and the tests were written against the CSV's spelling.
PROJECTION_COLUMNS = {
    "id": "id", "first_name": "first", "last_name": "last", "abbrev": "abbr",
    "team": "team", "division": "div", "record": "record", "points_played": "pts",
    "prev_score": "prev", "projected_score": "proj", "chance": "chance",
    "projected_rank": "rank", "from_season": "from_season",
    "to_season": "to_season", "min_points": "min_points",
}
PROJECTION_NUMERIC = {
    "pts": int, "prev": float, "proj": float, "chance": float, "rank": int,
    "from_season": int, "to_season": int, "min_points": int,
}

PLAYER_COLUMNS = {
    "id": "id", "first_name": "first", "last_name": "last", "jersey": "jersey",
    "team": "team", "abbrev": "abbr", "division": "div", "record": "record",
    "games_played": "games_played", "games_played_full": "games_played_full",
    "goals": "goals", "assists": "assists", "hockey_assists": "hockey_assists",
    "blocks": "blocks", "completions": "completions",
    "throw_attempts": "throw_attempts", "turnovers": "turnovers",
    "yards_thrown": "yards_thrown", "yards_received": "yards_received",
    "plus_minus": "plus_minus", "total_scores": "total_scores",
    "mvp_score": "mvp_score", "ranks": "ranks", "trend": "trend",
    "headshot": "headshot",
}
PLAYER_NUMERIC = {
    "games_played": int, "games_played_full": int, "goals": int, "assists": int,
    "hockey_assists": int, "blocks": int, "completions": int,
    "throw_attempts": int, "turnovers": int, "yards_thrown": int,
    "yards_received": int, "plus_minus": int, "total_scores": int,
    "mvp_score": float,
}


def _coerce(df: pd.DataFrame, numeric: dict) -> pd.DataFrame:
    """Force the numeric columns to numbers.

    PostgREST renders `numeric` columns as JSON strings, so mvp_score arrives as
    "146.1" and sorting it would order lexically - 9 above 146. Cheap insurance.
    """
    for col, kind in numeric.items():
        if col not in df.columns:
            df[col] = 0
        s = pd.to_numeric(df[col], errors="coerce").fillna(0)
        df[col] = s.astype("int64") if kind is int else s.astype(float)
    return df


class SupabaseStore(object):
    """A read-only, cached view of the Supabase tables."""

    def __init__(self, url="", key="", cache_seconds=CACHE_SECONDS, timeout=TIMEOUT):
        self.url = (url or "").rstrip("/")
        self.key = key or ""
        self.cache_seconds = cache_seconds
        self.timeout = timeout
        self._cache = {}
        # Seams, so the tests can drive this without a network or a clock.
        self._open = urllib.request.urlopen
        self._now = time.time

    @classmethod
    def from_env(cls):
        """Build from the environment - a Modal Secret in deployment, .env locally."""
        try:
            cache = int(os.environ.get("MVP_CACHE_SECONDS", CACHE_SECONDS))
        except ValueError:
            cache = CACHE_SECONDS
        return cls(
            url=os.environ.get("SUPABASE_URL", ""),
            # SUPABASE_KEY wins so a deployment can name it plainly; SUPABASE_ANON_KEY
            # is what .env already calls it, so both work with no edit.
            key=os.environ.get("SUPABASE_KEY") or os.environ.get("SUPABASE_ANON_KEY", ""),
            cache_seconds=cache,
        )

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    def clear_cache(self):
        self._cache = {}

    # ------------------------------------------------------------ transport
    def select(self, table, columns="*", order=None):
        """GET every row of one table, walking PostgREST's pages.

        Returns a list of dicts. Raises SupabaseError for anything else - a bad
        key, a missing table, a dead network.
        """
        if not self.configured:
            missing = [n for n, v in (("SUPABASE_URL", self.url),
                                      ("SUPABASE_KEY", self.key)) if not v]
            raise SupabaseError(
                "Supabase is not configured: set %s (see .env.example)"
                % ", ".join(missing))

        ck = (table, columns, order)
        hit = self._cache.get(ck)
        if hit and self._now() - hit[0] < self.cache_seconds:
            return hit[1]

        query = {"select": columns}
        if order:
            query["order"] = order
        url = "%s/rest/v1/%s?%s" % (self.url, table, urllib.parse.urlencode(query))

        rows, offset = [], 0
        while True:
            page = self._page(url, table, offset)
            rows.extend(page)
            # A short page is the last page. Content-Range would also tell us, but
            # this holds even when Supabase omits the total.
            if len(page) < PAGE:
                break
            offset += PAGE

        self._cache[ck] = (self._now(), rows)
        return rows

    def _page(self, url, table, offset):
        req = urllib.request.Request(url, method="GET", headers={
            "apikey": self.key,
            "Authorization": "Bearer " + self.key,
            "Accept": "application/json",
            "Range-Unit": "items",
            "Range": "%d-%d" % (offset, offset + PAGE - 1),
        })
        try:
            with self._open(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:300]
            except Exception:                                    # noqa: BLE001
                pass
            raise SupabaseError("reading %s from Supabase failed: %s %s %s"
                                % (table, e.code, e.reason, detail))
        except Exception as e:                                   # noqa: BLE001
            raise SupabaseError("reading %s from Supabase failed: %s" % (table, e))

        try:
            data = json.loads(body)
        except ValueError as e:
            raise SupabaseError("reading %s from Supabase failed: bad JSON (%s)"
                                % (table, e))
        if not isinstance(data, list):
            raise SupabaseError("reading %s from Supabase failed: %s"
                                % (table, str(data)[:300]))
        return data

    # ------------------------------------------------------------ frames
    def _frame(self, table, mapping, numeric, order):
        rows = self.select(table, columns=",".join(mapping), order=order)
        if not rows:
            raise SupabaseError(
                "the %s table in Supabase is empty - run push_supabase.py to load it"
                % table)
        df = pd.DataFrame(rows).rename(columns=mapping)
        for short in mapping.values():
            if short not in df.columns:
                df[short] = None
        df = _coerce(df, numeric)
        df["id"] = df["id"].astype(str)
        return df.set_index("id", drop=False).rename_axis("id")

    def projections(self) -> pd.DataFrame:
        """Next season's projections, indexed by player id, ranked column `rank`."""
        return self._frame("projections", PROJECTION_COLUMNS, PROJECTION_NUMERIC,
                           order="projected_rank.asc")

    def players(self) -> pd.DataFrame:
        """Current-season totals, indexed by player id, best MVP Score first."""
        return self._frame("players", PLAYER_COLUMNS, PLAYER_NUMERIC,
                           order="mvp_score.desc")

    def projection_seasons(self):
        """(from_season, to_season) as the loaded rows report them.

        Read from the data rather than the model bundle on purpose: this is the
        season the rows being served actually describe, which is what /health
        needs in order to show a stale table against a newer model.
        """
        df = self.projections()
        return int(df["from_season"].iloc[0]), int(df["to_season"].iloc[0])


# ==================================================================== artifact
def artifact_sklearn_version(path=None) -> Optional[str]:
    """The scikit-learn version recorded inside pipeline.joblib.

    sklearn stamps `_sklearn_version` into every estimator's pickled state, so
    this is the artifact's own account of what built it - not a number we typed
    and have to remember to update. modal_serve.py pins the image to exactly this,
    because a version mismatch can unpickle to something subtly wrong rather
    than failing outright.

    Returns None if the artifact does not say, which a caller should treat as a
    reason to stop rather than a reason to guess.
    """
    bundle = joblib.load(path or MODEL_PATH)
    if isinstance(bundle, dict) and bundle.get("sklearn_version"):
        return str(bundle["sklearn_version"])
    for key in ("score_model", "chance_model"):
        est = bundle.get(key) if isinstance(bundle, dict) else None
        if est is None:
            continue
        state = est.__getstate__() if hasattr(est, "__getstate__") else None
        if isinstance(state, dict) and state.get("_sklearn_version"):
            return str(state["_sklearn_version"])
    return None


# ==================================================================== data access
STORE = SupabaseStore.from_env()


def set_store(store):
    """Swap the Supabase store. Only the tests and modal_serve.py use this."""
    global STORE
    STORE = store
    return STORE


class Models:
    """The model bundle, loaded lazily so importing this module stays cheap.

    Only the pickle lives here. Rows are not cached at this level - SupabaseStore
    owns that TTL, so pushing new data shows up without restarting a container.
    """

    def __init__(self):
        self._bundle = None

    @property
    def bundle(self):
        if self._bundle is None:
            self._bundle = joblib.load(MODEL_PATH)
        return self._bundle

    @property
    def projections(self) -> pd.DataFrame:
        return STORE.projections()

    @property
    def players(self) -> pd.DataFrame:
        return STORE.players()

    @property
    def trained_through(self) -> int:
        return int(self.bundle["trained_through"])

    @property
    def target_season(self) -> int:
        return self.trained_through + 1


MODELS = Models()


# ==================================================================== auth
def require_key(key: Optional[str] = Security(_api_key_header)) -> str:
    """Constant-time check against MVP_API_KEY.

    Applied once, as a dependency on the router that carries every data route, so
    a new endpoint is protected by where it is declared rather than by whoever
    adds it remembering to ask. /health is the only route outside that router.
    """
    expected = os.environ.get("MVP_API_KEY", "")
    if not expected:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "server has no MVP_API_KEY configured")
    if not key or not _secrets.compare_digest(key, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "missing or invalid %s header" % API_KEY_NAME)
    return key


# ==================================================================== schemas
class SeasonStats(BaseModel):
    """One player-season of raw totals. Every field defaults to 0, so you can send
    only the stats you have - but a sparse line will project low, because the model
    reads the blanks as a player who did not do those things."""

    goals: float = 0
    assists: float = 0
    hockey_assists: float = 0
    blocks: float = 0
    callahans: float = 0
    callahans_thrown: float = 0
    completions: float = 0
    throw_attempts: float = 0
    throwaways: float = 0
    stalls: float = 0
    drops: float = 0
    catches: float = 0
    yards_thrown: float = 0
    yards_received: float = 0
    hucks_completed: float = 0
    hucks_attempted: float = 0
    o_points_played: float = 0
    o_points_scored: float = 0
    d_points_played: float = 0
    d_points_scored: float = 0
    pulls: float = 0
    ob_pulls: float = 0
    seconds_played: float = 0
    o_opportunities: float = 0
    o_opportunity_scores: float = 0
    d_opportunities: float = 0
    d_opportunity_stops: float = 0
    team_wins: float = 0
    team_losses: float = 0
    team_ties: float = 0
    team_standing: float = 0

    model_config = {
        "json_schema_extra": {
            "example": {
                "goals": 40, "assists": 69, "hockey_assists": 36, "blocks": 6,
                "completions": 430, "throw_attempts": 455, "throwaways": 22,
                "drops": 6, "catches": 380, "yards_thrown": 3364,
                "yards_received": 3657, "o_points_played": 246,
                "d_points_played": 5, "seconds_played": 14000,
                "team_wins": 11, "team_losses": 3,
            }
        }
    }


class Prediction(BaseModel):
    projected_score: float = Field(..., description="Projected MVP Score next season")
    chance_top5: float = Field(..., ge=0, le=1,
                               description="Calibrated probability of a top-%d season" % TOP_N)
    current_score: float = Field(..., description="MVP Score of the season supplied")
    target_season: int
    typical_error: float = Field(
        ..., description="Mean absolute error of projected_score, in MVP Score points")


class PlayerOut(BaseModel):
    id: str
    first: str
    last: str
    team: str
    abbr: str
    div: str
    points_played: int
    current_score: float
    projected_score: float
    chance_top5: float
    projected_rank: int


class SeasonPlayerOut(BaseModel):
    """A row of the completed season, straight from the dashboard's players table."""
    id: str
    first: str
    last: str
    jersey: str = ""
    team: str
    abbr: str
    div: str
    record: str = ""
    games_played: int
    goals: int
    assists: int
    hockey_assists: int
    blocks: int
    completions: int
    throw_attempts: int
    turnovers: int
    yards_thrown: int
    yards_received: int
    plus_minus: int
    total_scores: int
    mvp_score: float
    ranks: Dict[str, Any] = Field(
        default_factory=dict, description="Rank in each race, keyed by metric")
    trend: Dict[str, Any] = Field(
        default_factory=dict, description="Change in rank since last week, by metric")
    headshot: Optional[str] = None


class ComparePick(BaseModel):
    """Either an existing player id, or a name plus a stat line of your own."""
    player_id: Optional[str] = None
    name: Optional[str] = None
    stats: Optional[SeasonStats] = None


class CompareRequest(BaseModel):
    picks: List[Union[str, ComparePick]] = Field(
        ..., min_length=2, max_length=5,
        description="Two to five players: plain ids, or objects with a custom stat line")

    model_config = {"json_schema_extra": {
        "example": {"picks": ["khenke", "tdecraene", "aatkins", "jfairfax", "cyorgason"]}}}


class CompareEntry(BaseModel):
    rank: int
    player_id: Optional[str]
    name: str
    projected_score: float
    chance_top5: float
    current_score: float


class CompareResponse(BaseModel):
    target_season: int
    winner: str
    margin: float = Field(..., description="Projected points between first and second")
    within_noise: bool = Field(
        ..., description="True when the margin is smaller than the model's typical error")
    typical_error: float
    ranked: List[CompareEntry]


class RouteInfo(BaseModel):
    path: str
    method: str
    auth: bool = Field(..., description="True when this route needs the X-API-Key header")
    summary: str


class Index(BaseModel):
    service: str
    version: str
    docs: str = Field("/docs", description="Browsable OpenAPI docs, no key needed")
    auth_header: str = Field(API_KEY_NAME, description="Header the key goes in")
    routes: List[RouteInfo]


class Health(BaseModel):
    status: str
    source: str = Field("supabase", description="Where the rows came from")
    trained_through: int
    target_season: int
    players: int = Field(..., description="Rows in the projections table")
    season_players: int = Field(..., description="Rows in the current-season table")
    projections_season: int = Field(
        ..., description="Season the stored projections are for")
    stale: bool = Field(
        ..., description="True when Supabase holds projections older than the model")
    model_loaded: bool


# ==================================================================== scoring
MAE = 17.7      # leave-one-season-out mean absolute error; see README


def _frame(stats: SeasonStats) -> pd.DataFrame:
    row = {c: float(getattr(stats, c, 0) or 0) for c in FEATURE_TOTALS}
    return pd.DataFrame([row])


def _score(stats: SeasonStats) -> Prediction:
    df = _frame(stats)
    bundle = MODELS.bundle
    return Prediction(
        projected_score=round(float(bundle["score_model"].predict(df)[0]), 2),
        chance_top5=round(float(bundle["chance_model"].predict_proba(df)[0, 1]), 4),
        current_score=round(float(mvp_score(df, bundle["weights"]).iloc[0]), 2),
        target_season=MODELS.target_season,
        typical_error=MAE,
    )


def _player_row(player_id: str):
    proj = MODELS.projections
    if player_id not in proj.index:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "no player %r in the %d projections" % (player_id, MODELS.target_season))
    return proj.loc[player_id]


def _text(r, col: str) -> str:
    """A missing text cell as an empty string, not a NaN.

    Not defensive padding: the league has at least one mononym player, whose blank
    first name arrives as a float NaN and fails the response model outright.
    """
    v = r.get(col)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    return str(v)


def _as_player_out(r) -> PlayerOut:
    return PlayerOut(
        id=r["id"], first=_text(r, "first"), last=_text(r, "last"),
        team=_text(r, "team"), abbr=_text(r, "abbr"), div=_text(r, "div"),
        points_played=int(r["pts"]),
        current_score=float(r["prev"]), projected_score=float(r["proj"]),
        chance_top5=float(r["chance"]), projected_rank=int(r["rank"]))


def _as_season_player(r) -> SeasonPlayerOut:
    def txt(c):
        return _text(r, c)

    def obj(c):
        v = r.get(c)
        return v if isinstance(v, dict) else {}

    return SeasonPlayerOut(
        id=r["id"], first=txt("first"), last=txt("last"), jersey=txt("jersey"),
        team=txt("team"), abbr=txt("abbr"), div=txt("div"), record=txt("record"),
        games_played=int(r["games_played"]), goals=int(r["goals"]),
        assists=int(r["assists"]), hockey_assists=int(r["hockey_assists"]),
        blocks=int(r["blocks"]), completions=int(r["completions"]),
        throw_attempts=int(r["throw_attempts"]), turnovers=int(r["turnovers"]),
        yards_thrown=int(r["yards_thrown"]), yards_received=int(r["yards_received"]),
        plus_minus=int(r["plus_minus"]), total_scores=int(r["total_scores"]),
        mvp_score=float(r["mvp_score"]), ranks=obj("ranks"), trend=obj("trend"),
        headshot=txt("headshot") or None)


def _search(df: pd.DataFrame, term: str) -> pd.DataFrame:
    """Case-insensitive match across name, team and abbreviation."""
    if not term:
        return df
    s = term.lower()

    # fillna before astype: astype(str) would render a blank name as the literal
    # "nan", which a search for "an" would then match.
    def col(c):
        return df[c].fillna("").astype(str).str.lower()

    hit = (col("first").str.contains(s, na=False)
           | col("last").str.contains(s, na=False)
           | col("abbr").str.contains(s, na=False)
           | col("team").str.contains(s, na=False))
    return df[hit]


# ==================================================================== app
DESCRIPTION = """
Projects a UFA player's **MVP Score for next season** from their current season, and
the calibrated chance they finish in the league's top five.

Two random forests behind it, trained on every pair of consecutive player-seasons from
2021 to 2026. Judged by holding out a whole season at a time, the projection explains
about 45% of the variance in next season's score against 32% for assuming a player
simply repeats himself - **but its typical miss is 17.7 points of MVP Score**, which is
wider than most gaps between two players. Treat an ordering as a lean, not a result.

Rosters, projections and season totals are read live from Supabase; the projection
itself is computed here.

Authenticate with an `X-API-Key` header. Click **Authorize** to try these from here.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="UFA MVP Projection API",
        version="2.0.0",
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )
    if CORS_ORIGINS:
        app.add_middleware(
            CORSMiddleware, allow_origins=CORS_ORIGINS,
            allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.exception_handler(SupabaseError)
    def _supabase_down(request: Request, exc: SupabaseError):
        """One place to turn an unreachable database into an honest 503."""
        return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            content={"detail": str(exc)})

    @app.get("/", response_model=Index, tags=["meta"],
             summary="What this service is and where to go (no key required)")
    def index():
        """The bare URL, answered.

        FastAPI's default for `/` is a 22-byte `{"detail":"Not Found"}`, which is
        what anyone pasting the deployment URL into Postman or a browser sees
        first - and it reads as a dead service rather than as "you want /docs".

        Deliberately touches neither the key nor Supabase: this is the route you
        reach for when the other two are failing, so it must not fail with them.
        """
        return Index(
            service="UFA MVP Projection API",
            version=app.version,
            routes=[
                RouteInfo(path="/", method="GET", auth=False,
                          summary="This index"),
                RouteInfo(path="/health", method="GET", auth=False,
                          summary="Liveness, row counts, and whether the data is stale"),
                RouteInfo(path="/players", method="GET", auth=True,
                          summary="The projectable roster (search, limit)"),
                RouteInfo(path="/players/{player_id}", method="GET", auth=True,
                          summary="One player's stored projection"),
                RouteInfo(path="/season/players", method="GET", auth=True,
                          summary="Completed-season totals, best MVP Score first"),
                RouteInfo(path="/season/players/{player_id}", method="GET", auth=True,
                          summary="One player's completed season"),
                RouteInfo(path="/predict", method="POST", auth=True,
                          summary="Project any season stat line"),
                RouteInfo(path="/compare", method="POST", auth=True,
                          summary="Rank two to five players and name a winner"),
            ])

    @app.get("/health", response_model=Health, tags=["meta"],
             summary="Liveness check (no key required)")
    def health():
        """Unauthenticated, so uptime checks stay simple - but it does read Supabase,
        so point a monitor at it every few minutes, not every ten seconds."""
        try:
            proj = MODELS.projections
            season_rows = len(MODELS.players)
            stored_to = int(proj["to_season"].iloc[0])
            return Health(
                status="ok", source="supabase",
                trained_through=MODELS.trained_through,
                target_season=MODELS.target_season,
                players=len(proj), season_players=season_rows,
                projections_season=stored_to,
                stale=stored_to < MODELS.target_season,
                model_loaded=True)
        except SupabaseError as e:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "Supabase unavailable: %s" % e)
        except Exception as e:                                   # noqa: BLE001
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "models unavailable: %s" % e)

    # Everything below this line requires the key - it hangs off the router, not
    # off the individual endpoints, so an added route cannot forget it.
    api = APIRouter(dependencies=[Depends(require_key)])

    @api.get("/players", response_model=List[PlayerOut], tags=["players"],
             summary="The projectable roster")
    def players(search: str = Query("", description="Case-insensitive name or team filter"),
                limit: int = Query(50, ge=1, le=1000)):
        df = _search(MODELS.projections, search).sort_values("rank").head(limit)
        return [_as_player_out(r) for _, r in df.iterrows()]

    @api.get("/players/{player_id}", response_model=PlayerOut, tags=["players"],
             summary="One player's stored projection")
    def player(player_id: str):
        return _as_player_out(_player_row(player_id))

    @api.get("/season/players", response_model=List[SeasonPlayerOut], tags=["season"],
             summary="Completed-season totals, best MVP Score first")
    def season_players(
            search: str = Query("", description="Case-insensitive name or team filter"),
            limit: int = Query(50, ge=1, le=1000)):
        """The season that has already happened - what /players projects forward from."""
        df = _search(MODELS.players, search).sort_values("mvp_score", ascending=False)
        return [_as_season_player(r) for _, r in df.head(limit).iterrows()]

    @api.get("/season/players/{player_id}", response_model=SeasonPlayerOut,
             tags=["season"], summary="One player's completed season")
    def season_player(player_id: str):
        df = MODELS.players
        if player_id not in df.index:
            raise HTTPException(status.HTTP_404_NOT_FOUND,
                                "no player %r in the season table" % player_id)
        return _as_season_player(df.loc[player_id])

    @api.post("/predict", response_model=Prediction, tags=["predict"],
              summary="Project any season stat line")
    def predict(stats: SeasonStats):
        """Send a season of raw totals - real or invented - and get the projection."""
        return _score(stats)

    @api.post("/compare", response_model=CompareResponse, tags=["predict"],
              summary="Rank two to five players and name a winner")
    def compare(req: CompareRequest):
        entries = []
        for pick in req.picks:
            if isinstance(pick, str):
                r = _player_row(pick)
                entries.append(CompareEntry(
                    rank=0, player_id=r["id"],
                    name=(_text(r, "first") + " " + _text(r, "last")).strip(),
                    projected_score=float(r["proj"]), chance_top5=float(r["chance"]),
                    current_score=float(r["prev"])))
            elif pick.player_id and not pick.stats:
                r = _player_row(pick.player_id)
                entries.append(CompareEntry(
                    rank=0, player_id=r["id"],
                    name=pick.name or (_text(r, "first") + " " + _text(r, "last")).strip(),
                    projected_score=float(r["proj"]), chance_top5=float(r["chance"]),
                    current_score=float(r["prev"])))
            elif pick.stats:
                p = _score(pick.stats)
                entries.append(CompareEntry(
                    rank=0, player_id=pick.player_id,
                    name=pick.name or pick.player_id or "custom stat line",
                    projected_score=p.projected_score, chance_top5=p.chance_top5,
                    current_score=p.current_score))
            else:
                raise HTTPException(
                    422,
                    "each pick needs a player_id or a stats object")

        entries.sort(key=lambda e: -e.projected_score)
        for i, e in enumerate(entries, 1):
            e.rank = i
        margin = round(entries[0].projected_score - entries[1].projected_score, 2)
        return CompareResponse(
            target_season=MODELS.target_season, winner=entries[0].name, margin=margin,
            within_noise=margin < MAE, typical_error=MAE, ranked=entries)

    app.include_router(api)
    return app


app = create_app()
