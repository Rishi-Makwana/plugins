"""Dotted-path lookup into parsed JSON. Never raises; misses return the default."""
from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"\[(\d*)\]|([^.\[\]]+)")
_CACHE: dict[str, tuple] = {}

_EACH = object()  # the "[]" token: map the rest of the path over a list


def _tokens(path: str) -> tuple:
    toks = _CACHE.get(path)
    if toks is None:
        out = []
        if path != "$":
            for m in _TOKEN_RE.finditer(path):
                idx, key = m.group(1), m.group(2)
                if key is not None:
                    out.append(key)
                elif idx == "":
                    out.append(_EACH)
                else:
                    out.append(int(idx))
        toks = tuple(out)
        _CACHE[path] = toks
    return toks


def _walk(cur, toks, default):
    for i, tok in enumerate(toks):
        if tok is _EACH:
            if not isinstance(cur, list):
                return default
            rest = toks[i + 1:]
            out = []
            for item in cur:
                v = item if not rest else _walk(item, rest, None)
                if v is not None:
                    out.append(v)
            if out and all(isinstance(v, list) for v in out):
                out = [x for sub in out for x in sub]  # flatten one level
            return out
        if isinstance(tok, int):
            if not isinstance(cur, list) or not -len(cur) <= tok < len(cur):
                return default
            cur = cur[tok]
        else:
            if not isinstance(cur, dict) or tok not in cur:
                return default
            cur = cur[tok]
    return default if cur is None else cur


def get(obj, path: str, default=None):
    try:
        # A projected record keys on the literal dotted path (§3.1), so an exact
        # key match wins before we try to traverse it as a path.
        if isinstance(obj, dict) and path in obj:
            v = obj[path]
            return default if v is None else v
        return _walk(obj, _tokens(path), default)
    except Exception:
        return default
