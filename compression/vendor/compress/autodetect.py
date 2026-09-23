"""Guess a preset when nothing sniffed. Used only when preset is None."""
from __future__ import annotations

import re
from collections import Counter

from .paths import get

# name, ordered vocabulary (index = rank), values treated as never-drop.
# Byte-identical to compress_check.py's LEXICONS — copied, not retyped (§3.5).
LEXICONS = [
    ("severity", ["critical", "high", "medium", "moderate", "low", "negligible",
                  "info", "informational", "unknown"], {"critical", "high"}),
    ("sonar",    ["blocker", "critical", "major", "minor", "info"], {"blocker", "critical"}),
    ("sarif",    ["error", "warning", "note", "none"], {"error"}),
    ("log",      ["fatal", "panic", "error", "warn", "warning", "info", "debug", "trace"],
                 {"fatal", "panic", "error"}),
    ("test",     ["failed", "failure", "error", "passed", "pass", "skipped", "pending"],
                 {"failed", "failure", "error"}),
]

_SCORE_RE = re.compile(r"(?i)score|cvss|severity|priority|rating")
_MAX_FIELDS = 8


def find_records(obj):
    """Largest array of ≥3 dicts anywhere in the tree → (path, records) or None."""
    found = []

    def walk(o, path, depth):
        if isinstance(o, list):
            if len(o) >= 3 and sum(isinstance(x, dict) for x in o) >= 0.8 * len(o):
                found.append((path or "$", depth, o))
            for i, x in enumerate(o[:40]):
                walk(x, f"{path}[{i}]", depth + 1)
        elif isinstance(o, dict):
            for k, v in o.items():
                walk(v, f"{path}.{k}" if path else k, depth + 1)

    walk(obj, "", 0)
    if not found:
        return None
    path, _, recs = max(found, key=lambda t: (len(t[2]), -t[1]))
    return path, recs


def _candidate_fields(records):
    """Scalar keys present in ≥80% of records, one nesting level allowed."""
    present: Counter = Counter()
    kinds: dict[str, set] = {}
    for rec in records:
        for k, v in rec.items():
            if isinstance(v, (str, int, float, bool)):
                present[k] += 1
                kinds.setdefault(k, set()).add(type(v).__name__)
            elif isinstance(v, dict):
                for k2, v2 in v.items():
                    if isinstance(v2, (str, int, float, bool)):
                        present[f"{k}.{k2}"] += 1
                        kinds.setdefault(f"{k}.{k2}", set()).add(type(v2).__name__)
    floor = 0.8 * len(records)
    return [k for k, n in present.items() if n >= floor], kinds


def _cardinality(records, field):
    return len({str(get(r, field)) for r in records})


def _pick_rank(records, fields):
    best = None
    for f in fields:
        vals = Counter(str(get(r, f) or "").lower() for r in records)
        distinct = set(vals) - {""}
        if not distinct or len(distinct) > 12:
            continue
        for name, lex, alerts in LEXICONS:
            hit = distinct & set(lex)
            if hit and len(hit) >= 0.6 * len(distinct):
                score = sum(vals[v] for v in hit)
                if best is None or score > best[0]:
                    best = (score, f, name, lex, alerts)
    return best


def _pick_score(records, fields):
    for f in fields:
        if not _SCORE_RE.search(f):
            continue
        nums = [get(r, f) for r in records]
        if sum(1 for v in nums if isinstance(v, (int, float)) and not isinstance(v, bool)) \
                >= 0.5 * len(records):
            return f
    return None


def _top_decile(records, field):
    nums = sorted(float(v) for v in (get(r, field) for r in records)
                  if isinstance(v, (int, float)) and not isinstance(v, bool))
    if not nums:
        return None
    return nums[int(0.9 * (len(nums) - 1))]


def detect(data) -> dict:
    """A synthesised preset. Always returns something renderable."""
    hit = find_records(data)
    if not hit:
        return {"name": "auto", "kind": "records", "rows_path": ["$"], "fields": [],
                "rank": {"path": None, "order": [], "default": 0},
                "alert": [{"rank_tier": -1}], "group_by": None}
    path, records = hit
    fields, _kinds = _candidate_fields(records)
    # short names first, then high cardinality
    fields = sorted(fields, key=lambda f: (len(f), -_cardinality(records, f)))[:_MAX_FIELDS]

    rank_hit = _pick_rank(records, fields)
    score_field = _pick_score(records, fields)

    alert: list[dict] = []
    if rank_hit:
        _, rank_field, _name, lex, alert_vals = rank_hit
        rank = {"path": rank_field, "order": list(lex), "default": len(lex)}
        present_alerts = sorted(alert_vals)
        if present_alerts:
            alert.append({"path": rank_field, "in": present_alerts})
    else:
        rank_field = None
        rank = {"path": None, "order": [], "default": 0}
    if score_field:
        cut = _top_decile(records, score_field)
        if cut is not None:
            alert.append({"path": score_field, "gte": cut})
    if not alert:
        alert = [{"rank_tier": -1}]  # no tier is -1: nothing is an alert

    group = None
    lo, hi = 2, max(2, len(records) // 3)
    best_card = None
    for f in fields:
        if f in (rank_field, score_field):
            continue
        if not all(isinstance(get(r, f), (str, type(None))) for r in records):
            continue
        card = _cardinality(records, f)
        if lo <= card <= hi and (best_card is None or card < best_card):
            group, best_card = f, card

    return {"name": "auto", "kind": "records", "rows_path": [path],
            "fields": fields or ["id"], "rank": rank,
            "score": {"path": score_field, "desc": True} if score_field else None,
            "alert": alert, "group_by": group, "identity": None}
