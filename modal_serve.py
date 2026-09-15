# -*- coding: utf-8 -*-
"""Deploy the FastAPI app in serve.py to Modal.

    modal secret create ufa-mvp-api-key MVP_API_KEY=<your key>              # once
    modal secret create ufa-supabase \
        SUPABASE_URL=https://<ref>.supabase.co SUPABASE_ANON_KEY=<anon key>  # once
    modal serve modal_serve.py     # temporary URL, live-reloads, dies on Ctrl-C
    modal deploy modal_serve.py    # permanent URL

Three files ship in the image and nothing else:

    serve.py         the FastAPI app and its Supabase reader
    pipeline_def.py  the feature transformer the pickle refers to by name
    pipeline.joblib  the two fitted pipelines, their weights, and the version stamp

pipeline_def.py is not optional even though nothing here imports it: unpickling an
sklearn Pipeline reconstructs the custom transformer by module path, so the class
has to be importable inside the container or the load fails.

Two secrets, because they have different lifetimes and different blast radii. The
API key is ours to rotate; the Supabase credentials belong to the database. The
anon key is the correct one - schema.sql gives anon a read-only SELECT policy on
these tables and the API never writes. The service_role key must never reach here.

Cost control, since an open endpoint on a pay-per-use platform is the thing to get
wrong here:

  * min_containers=0     scale to zero - an idle deployment bills nothing at all
  * scaledown_window=60  a container that goes a minute without work shuts down
  * max_containers=2     a traffic spike (or a scraper) cannot fan out past two
  * CPU-only, 2 GB       no GPU is involved anywhere in this project
  * X-API-Key on every endpoint except /health, enforced by the router in serve.py
  * MVP_CACHE_SECONDS    a container re-reads Supabase at most once every 5 min

/health is deliberately unauthenticated so uptime checks stay simple, but it wakes a
container and reads Supabase - point a monitor at it every few minutes, not every
ten seconds.

Only the model ships in the image; rows do not. That split is the point of this
deployment: retraining means `modal deploy`, but new numbers mean
`python push_supabase.py` and nothing else.
"""
import os

import joblib
import modal

APP_NAME = "ufa-mvp-api"
API_KEY_SECRET = "ufa-mvp-api-key"
# Its own secret, not the workspace's older `supabase-credentials`: that one holds
# a different project's URL, and the tables this app reads do not exist there. The
# failure mode was a 404 from PostgREST that looked exactly like an unapplied
# schema, so the name being plausible was the whole problem.
SUPABASE_SECRET = "ufa-supabase"

HERE = os.path.dirname(os.path.abspath(__file__))
PIPELINE = "pipeline.joblib"
REMOTE_DIR = "/srv/model"
REMOTE_PIPELINE = REMOTE_DIR + "/" + PIPELINE


def pinned_sklearn() -> str:
    """The scikit-learn version recorded inside pipeline.joblib.

    Read from the artifact rather than typed here, because a hand-written pin is a
    number someone has to remember to change after retraining on a new machine -
    and getting it wrong is the quiet kind of wrong. A version mismatch can
    unpickle an estimator into something subtly different rather than failing
    outright, so this pin is load-bearing.

    sklearn stamps `_sklearn_version` into every estimator's pickled state;
    train_mvp.py also writes it as a plain `sklearn_version` key. Prefer the plain
    key, fall back to the stamp, and refuse to guess if neither is there.

    Modal re-imports this module *inside* the container to find the function, so
    this runs in both places - not only at deploy time. In the container the
    artifact sits at MVP_MODEL_PATH rather than beside this file, and the image
    has already been built and pinned, so the answer is no longer needed: report
    what is installed instead of reading a 20 MB pickle on every cold start.
    """
    path = os.path.join(HERE, PIPELINE)
    if not os.path.exists(path):
        import sklearn
        return sklearn.__version__

    bundle = joblib.load(path)
    if isinstance(bundle, dict) and bundle.get("sklearn_version"):
        return str(bundle["sklearn_version"])
    for key in ("score_model", "chance_model"):
        est = bundle.get(key) if isinstance(bundle, dict) else None
        state = est.__getstate__() if hasattr(est, "__getstate__") else None
        if isinstance(state, dict) and state.get("_sklearn_version"):
            return str(state["_sklearn_version"])
    raise SystemExit(
        "%s records no scikit-learn version - re-run train_mvp.py so the pin can "
        "be read from the artifact instead of guessed" % PIPELINE)


SKLEARN_VERSION = pinned_sklearn()

app = modal.App(APP_NAME)

# The sklearn pin comes from the artifact above. The rest are pinned to what the
# models were built against locally, for the same reason.
image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install(
        "fastapi[standard]==0.141.1",
        "scikit-learn==" + SKLEARN_VERSION,
        "pandas==2.2.3",
        "numpy==2.2.6",
        "scipy==1.18.1",
        "joblib==1.6.0",
    )
    .env({
        "MVP_MODEL_PATH": REMOTE_PIPELINE,
        # How long a warm container may serve rows before re-reading Supabase.
        # Push new data and it appears within this window, with no redeploy.
        "MVP_CACHE_SECONDS": "300",
    })
    # copy=True bakes the model into the image layer instead of mounting at runtime,
    # so a cold start has nothing to download.
    .add_local_file(os.path.join(HERE, PIPELINE), REMOTE_PIPELINE, copy=True)
    # The two Python sources. serve.py is the app; pipeline_def.py is what the
    # pickle names when it rebuilds the transformer.
    .add_local_python_source("pipeline_def", "serve")
)


@app.function(
    image=image,
    secrets=[
        modal.Secret.from_name(API_KEY_SECRET),     # MVP_API_KEY
        modal.Secret.from_name(SUPABASE_SECRET),    # SUPABASE_URL, SUPABASE_ANON_KEY
    ],
    min_containers=0,
    scaledown_window=60,
    max_containers=2,
    cpu=1.0,
    memory=2048,
    timeout=120,
)
@modal.concurrent(max_inputs=20)     # one container serves 20 requests at once
@modal.asgi_app()
def fastapi_app():
    # Imported here, inside the function, not at module scope: this body runs in
    # the container, where serve.py's dependencies and the Secrets both exist. At
    # module scope it would also run on the deploying machine, which does not need
    # FastAPI installed to push an image.
    import serve

    # serve.py builds its store when it is imported, which on a cold start can be
    # before the Secrets are readable; rebuild it here, where they are.
    serve.set_store(serve.SupabaseStore.from_env())
    return serve.create_app()
