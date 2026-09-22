"""Predicate vocabulary shared by sniff blocks, alert rules and fetch filters."""
from __future__ import annotations

import re

from .paths import get

_RE_CACHE: dict[str, object] = {}
_OPS = ("in", "eq", "gte", "lte", "matches", "exists")


def _num(v):
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    try:
        return float(str(v).strip())
    except Exception:
        return None


def _norm(v):
    return v.strip().lower() if isinstance(v, str) else v


def _compiled(pattern: str):
    rx = _RE_CACHE.get(pattern)
    if rx is None:
        try:
            rx = re.compile(pattern)
        except re.error:
            rx = False  # bad regex never matches
        _RE_CACHE[pattern] = rx
    return rx


def evaluate(record: dict, pred: dict, rank_tier: int) -> bool:
    try:
        if "rank_tier" in pred:
            return rank_tier == pred["rank_tier"]
        op = next((k for k in _OPS if k in pred), None)
        if op is None:
            return False  # unknown key
        value = get(record, pred.get("path", "$"))
        if op == "in":
            want = pred["in"]
            if not isinstance(want, (list, tuple, set)):
                return False
            return _norm(value) in {_norm(x) for x in want}
        if op == "eq":
            return value == pred["eq"]
        if op in ("gte", "lte"):
            a, b = _num(value), _num(pred[op])
            if a is None or b is None:
                return False
            return a >= b if op == "gte" else a <= b
        if op == "matches":
            rx = _compiled(str(pred["matches"]))
            return bool(rx and rx.search(str(value)))
        if op == "exists":
            return (value is not None) == bool(pred["exists"])
    except Exception:
        return False
    return False
