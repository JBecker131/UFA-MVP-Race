# -*- coding: utf-8 -*-
"""Wrap the Artifact HTML into a standalone page for static hosting (Vercel).

The Artifact runtime injects a <!doctype>, a <head> and a small CSS reset around
the file it publishes, so ufa-mvp-race.html is a fragment: no <html>, no <head>,
no body margin reset. This script rebuilds those parts so the same markup works
as an ordinary web page.

It also bakes the Supabase URL and anon key from .env into the page, so the
deployed site reads the season from Supabase rather than the frozen snapshot.
Both are public values - the anon key is read-only under row-level security.

    python make_site.py      ->  site/index.html
"""
import base64, io, json, os, re

import env

HERE = os.path.dirname(os.path.abspath(__file__))
SRC  = os.path.join(HERE, "ufa-mvp-race.html")
OUT  = os.path.join(HERE, "site", "index.html")

DESCRIPTION = ("Five races down an ultimate field from the real 2025 UFA season - plus a fitted "
               "MVP Score that shows how Tobe Decraene actually got there.")
FAVICON = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'%3E"
           "%3Ctext y='.9em' font-size='90'%3E%F0%9F%A5%8F%3C/text%3E%3C/svg%3E")

# the reset the Artifact runtime supplies; reproduced so the page looks identical off-platform
RESET = """    <style>
      :root { color-scheme: light dark; }
      body { margin: 0; }
      img { max-width: 100%; }
      [hidden] { display: none !important; }
    </style>"""


def is_service_role(v):
    """True if v is a Supabase key that can write. Cheap, no network."""
    if v.startswith("sb_secret_"):
        return True
    parts = v.split(".")                      # legacy keys are unsigned-readable JWTs
    if len(parts) != 3:
        return False
    try:
        pad = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.urlsafe_b64decode(pad)).get("role") == "service_role"
    except Exception:
        return False


def supabase_tag():
    """<script> that hands the page its Supabase credentials, or a note if unset."""
    cfg = env.load("SUPABASE_URL", "SUPABASE_ANON_KEY", required=False)
    url, key = cfg["SUPABASE_URL"], cfg["SUPABASE_ANON_KEY"]
    if not (url and key):
        print("warning: SUPABASE_URL / SUPABASE_ANON_KEY unset in .env - "
              "the page will render its build-time snapshot instead")
        return "    <!-- no Supabase credentials at build time; see .env.example -->"

    # Both values are about to be published in site/index.html, so refuse
    # anything that isn't the pair we expect. Pasting the service_role key into
    # either field would otherwise ship a write-capable credential to every
    # visitor - the one mistake here that actually costs something.
    if not url.startswith("https://") or "supabase" not in url:
        raise SystemExit("SUPABASE_URL is not a project URL (expected "
                         "https://<ref>.supabase.co) - refusing to publish it")
    if is_service_role(key) or is_service_role(url):
        raise SystemExit("that is the service_role key, not the anon key - "
                         "refusing to publish it; check .env against .env.example")

    return ('    <script>window.UFA_SUPABASE = %s;</script>'
            % json.dumps({"url": url, "key": key}))


def main():
    body = io.open(SRC, encoding="utf-8").read()

    m = re.search(r"<title>(.*?)</title>", body, re.S)
    title = m.group(1).strip() if m else "UFA MVP Race"
    body = body.replace(m.group(0), "", 1) if m else body

    # hoist the <link> tags (fonts) into the real <head>
    links = re.findall(r'^\s*<link\b[^>]*>\s*$', body, re.M)
    for l in links:
        body = body.replace(l, "", 1)

    head = "\n".join(["    " + l.strip() for l in links])
    page = (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "  <head>\n"
        '    <meta charset="utf-8">\n'
        '    <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        "    <title>%s</title>\n"
        '    <meta name="description" content="%s">\n'
        '    <meta property="og:title" content="%s">\n'
        '    <meta property="og:description" content="%s">\n'
        '    <meta property="og:type" content="website">\n'
        '    <link rel="icon" href="%s">\n'
        "%s\n%s\n%s\n"
        "  </head>\n"
        "  <body>\n%s\n  </body>\n"
        "</html>\n"
    ) % (title, DESCRIPTION, title, DESCRIPTION, FAVICON, head, RESET,
         supabase_tag(), body.strip())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(page)
    print("wrote %s  (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024))
    print("title: %s | hoisted %d <link> tags" % (title, len(links)))


if __name__ == "__main__":
    main()
