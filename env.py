# -*- coding: utf-8 -*-
"""Tiny .env reader, shared by push_supabase.py and make_site.py.

No dependency on python-dotenv: the file format we need is KEY=value, one per
line, with # comments. Real environment variables win over the file, so CI and
Vercel can override without editing anything.
"""
import io, os

HERE = os.path.dirname(os.path.abspath(__file__))
FILES = (".env", ".env.local")


def load(*names, **kw):
    """Read .env (then .env.local) and return the requested keys.

    Raises SystemExit naming every key that came back empty, so a missing
    credential fails loudly instead of producing a half-wired page. Pass
    required=False for callers that have a sensible answer without them.
    """
    required = kw.pop("required", True)
    values = {}
    for fn in FILES:
        path = os.path.join(HERE, fn)
        if not os.path.exists(path):
            continue
        for line in io.open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            values[k.strip()] = v.strip().strip('"').strip("'")

    out = {n: os.environ.get(n) or values.get(n, "") for n in names}
    missing = [n for n in names if not out[n]]
    if missing and required:
        raise SystemExit(
            "missing %s - set %s in .env (see .env.example)"
            % ("it" if len(missing) == 1 else "them", ", ".join(missing)))
    return out
