# -*- coding: utf-8 -*-
"""Wrap the Artifact HTML into a standalone page for static hosting (Vercel).

The Artifact runtime injects a <!doctype>, a <head> and a small CSS reset around
the file it publishes, so ufa-mvp-race.html is a fragment: no <html>, no <head>,
no body margin reset. This script rebuilds those parts so the same markup works
as an ordinary web page.

    python make_site.py      ->  site/index.html
"""
import io, os, re

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
        "%s\n%s\n"
        "  </head>\n"
        "  <body>\n%s\n  </body>\n"
        "</html>\n"
    ) % (title, DESCRIPTION, title, DESCRIPTION, FAVICON, head, RESET, body.strip())

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    io.open(OUT, "w", encoding="utf-8", newline="\n").write(page)
    print("wrote %s  (%.0f KB)" % (OUT, os.path.getsize(OUT) / 1024))
    print("title: %s | hoisted %d <link> tags" % (title, len(links)))


if __name__ == "__main__":
    main()
