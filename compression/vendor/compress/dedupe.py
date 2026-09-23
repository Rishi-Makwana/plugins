"""Collapse identical records, keeping the most severe survivor.

Called AFTER classify_all, so every record already carries a tier/alert in
`_m`. "First occurrence wins" (spec) is what breaks ties; it never overrides
severity — a later duplicate that is more severe, or alert-worthy while the
earlier one isn't, must not vanish. The invariant ("compression may hide
records, never their existence") outranks literal first-occurrence order when
identity-colliding records disagree, which real tool output can do (e.g. a
finding reported once per project, or malformed rows sharing a blank identity).
"""
from __future__ import annotations

import hashlib
import json

from .classify import ident_part
from .paths import get


def _hashable(v):
    return json.dumps(v, sort_keys=True, default=str) if isinstance(v, (list, dict)) else v


def _rank(rec: dict, index: int) -> tuple:
    """(tier, not-alert, index) — lower sorts as more severe / more deserving
    of survival. Most severe tier wins outright; on a tied tier, an alert-
    worthy member outranks one that isn't, so the record that actually carries
    the alerting content survives, not just its flag. First occurrence breaks
    a full tie.
    """
    m = rec.get("_m") or {}
    return (m.get("tier", 0), 0 if m.get("alert") else 1, index)


def _is_alert(rec: dict) -> bool:
    return bool((rec.get("_m") or {}).get("alert"))


def dedupe(projected: list[dict], identity: list[str] | None) -> tuple[list[dict], int]:
    raw_total = len(projected)
    order: list[object] = []
    groups: dict[object, list[dict]] = {}
    for rec in projected:
        if identity:
            # same canonicalisation as the stored `ident` (classify.ident_of):
            # if these two disagree, `fetch --ids` resolves to a record other
            # than the one dedupe kept.
            key = tuple(ident_part(get(rec, p)) for p in identity)
        else:
            body = {k: v for k, v in rec.items() if not k.startswith("_")}
            key = hashlib.sha1(
                json.dumps(body, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()
        # Alert-worthiness is part of the key: an alerting record and a quiet
        # one are not interchangeable, so collapsing them would have to either
        # drop the alerting content or flag a survivor whose own data does not
        # justify the flag. Neither is honest — keep both instead.
        key = (key, _is_alert(rec))
        if key not in groups:
            order.append(key)
            groups[key] = []
        groups[key].append(rec)

    kept: list[dict] = []
    for key in order:
        members = groups[key]
        # One pass, not min()+.index() per candidate — that pairing is O(k²)
        # per group, and a single blank/missing identity (malformed input) can
        # collapse the entire input into one group. The un-classified case
        # (no `_m` yet) has every _rank equal on tier/alert, so this degrades
        # to plain first-occurrence, matching the pre-severity-aware behaviour.
        survivor, best = members[0], _rank(members[0], 0)
        for i, r in enumerate(members):
            cand = _rank(r, i)
            if cand < best:
                survivor, best = r, cand
        # No flag forcing: every member of a group now shares the same alert
        # state by construction, so the survivor's own data always justifies it.
        if len(members) > 1:
            survivor["_n"] = len(members)
        kept.append(survivor)
    return kept, raw_total
