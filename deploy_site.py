# -*- coding: utf-8 -*-
"""Rebuild site/index.html and publish it to Vercel.

    python deploy_site.py

Needs VERCEL_TOKEN in .env - from https://vercel.com/account/tokens. The CLI's
interactive `login` opens a browser, which cannot work from a non-TTY shell, so
the token is how an automated run authenticates instead.

Runs make_site.py first, on purpose: the deployed page and the source page drifted
once already - Vercel was serving a build with no Pick five panel and no Supabase
config, months behind ufa-mvp-race.html - and the only way that happens is
deploying whatever happened to be in site/ rather than rebuilding it. One command,
so the two cannot disagree.

The token is scrubbed from everything this prints.
"""
import os
import re
import subprocess
import sys

import env

HERE = os.path.dirname(os.path.abspath(__file__))


def run(args, secrets, label, capture=True):
    print("\n== %s ==" % label)
    proc = subprocess.run(args, capture_output=capture, text=True, shell=True, cwd=HERE,
                          env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    out = (proc.stdout or "") + (proc.stderr or "")
    for s in secrets:
        if s:
            out = out.replace(s, "<redacted>")
    for line in out.splitlines():
        if line.strip() and not line.startswith(("npm notice", "npm warn")):
            print("  " + line)
    return proc.returncode, out


def main():
    token = env.load("VERCEL_TOKEN")["VERCEL_TOKEN"]
    hide = [token]
    # The CLI reads this, so the token never appears in a command line or a process
    # listing - which `--token` would put there.
    os.environ["VERCEL_TOKEN"] = token

    rc, _ = run([sys.executable, "make_site.py"], hide, "rebuilding site/index.html")
    if rc:
        raise SystemExit("make_site.py failed (exit %d)" % rc)

    rc, out = run(["npx", "--yes", "vercel@latest", "deploy", "--prod", "--yes"],
                  hide, "deploying to Vercel")
    if rc:
        raise SystemExit("vercel deploy failed (exit %d)" % rc)

    urls = re.findall(r"https://[\w.-]+\.vercel\.app", out)
    if urls:
        print("\nproduction: %s" % urls[-1])
    return 0


if __name__ == "__main__":
    sys.exit(main())
