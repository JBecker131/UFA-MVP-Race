# -*- coding: utf-8 -*-
"""Deploy supabase/functions/mvp-proxy and point it at the live Modal API.

    python deploy_proxy.py

Needs SUPABASE_ACCESS_TOKEN in .env - a personal access token from
https://supabase.com/dashboard/account/tokens. The CLI's interactive `login`
opens a browser, which cannot work from a non-TTY shell, so the token is how an
automated run authenticates instead.

Reads MVP_API_KEY and SUPABASE_URL from .env and sets them as function secrets,
so the key lives on Supabase and never reaches the browser. Nothing secret is
printed: the CLI's own output is scrubbed before it is shown.

Docker is not required - `--use-api` bundles the function server-side.
"""
import os
import re
import subprocess
import sys

import env

HERE = os.path.dirname(os.path.abspath(__file__))
MODAL_URL = "https://jbecke20--ufa-mvp-api-fastapi-app.modal.run"
FUNCTION = "mvp-proxy"


def project_ref(supabase_url: str) -> str:
    """The <ref> in https://<ref>.supabase.co - what the CLI wants for --project-ref."""
    m = re.match(r"https://([a-z0-9]+)\.supabase\.co", supabase_url.rstrip("/"))
    if not m:
        raise SystemExit("cannot read a project ref out of SUPABASE_URL=%r" % supabase_url)
    return m.group(1)


def run(args, secrets, label):
    """Run one CLI command, scrubbing every secret out of whatever it prints."""
    print("\n== %s ==" % label)
    proc = subprocess.run(
        ["npx", "--yes", "supabase@latest"] + args,
        capture_output=True, text=True, shell=True, cwd=HERE,
        env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    out = (proc.stdout or "") + (proc.stderr or "")
    for s in secrets:
        if s:
            out = out.replace(s, "<redacted>")
    # npm's own chatter drowns the actual result
    for line in out.splitlines():
        if line.strip() and not line.startswith(("npm notice", "npm warn")):
            print("  " + line)
    return proc.returncode


def main():
    cfg = env.load("SUPABASE_URL", "MVP_API_KEY", "SUPABASE_ACCESS_TOKEN")
    url, key, token = (cfg["SUPABASE_URL"], cfg["MVP_API_KEY"],
                       cfg["SUPABASE_ACCESS_TOKEN"])
    ref = project_ref(url)
    os.environ["SUPABASE_ACCESS_TOKEN"] = token
    hide = [token, key]

    print("project ref : %s" % ref)
    print("modal url   : %s" % MODAL_URL)
    print("api key     : %d chars (never printed)" % len(key))

    rc = run(["secrets", "set",
              "MODAL_API_URL=" + MODAL_URL,
              "MODAL_API_KEY=" + key,
              "--project-ref", ref],
             hide, "setting function secrets")
    if rc:
        raise SystemExit("secrets set failed (exit %d)" % rc)

    # --no-verify-jwt: the dashboard is a public page with no login, so the
    # function has to be callable without a Supabase session. Its own route
    # allowlist, body cap and per-IP rate limit are the protections that matter.
    # --use-api: bundle server-side, so this works without Docker installed.
    rc = run(["functions", "deploy", FUNCTION,
              "--no-verify-jwt", "--project-ref", ref, "--use-api"],
             hide, "deploying %s" % FUNCTION)
    if rc:
        raise SystemExit("functions deploy failed (exit %d)" % rc)

    print("\ndeployed: %s/functions/v1/%s" % (url.rstrip("/"), FUNCTION))


if __name__ == "__main__":
    sys.exit(main())
