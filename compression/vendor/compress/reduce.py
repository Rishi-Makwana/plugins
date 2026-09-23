"""Fit records into a byte budget: alerts first, then round-robin across groups."""
from __future__ import annotations

import json
import re

from .paths import get

FOOTER_RESERVE = 1024
FOOTER_FLOOR = 768      # measured: real footers run 648-738 bytes

_SKIP = ("shown", "_m")  # render bookkeeping, never serialised


def bytelen(text: str) -> int:
    """max_bytes is a BYTE budget. len() counts characters, and one CJK or
    accented character is 2-4 UTF-8 bytes, so measuring in characters lets a
    non-ASCII payload overrun the stated budget by half again as much.
    """
    return len(text.encode("utf-8", "replace"))


def truncate_bytes(text: str, limit: int) -> str:
    """Cut to at most `limit` UTF-8 bytes without splitting a character."""
    if limit <= 0:
        return ""
    raw = text.encode("utf-8", "replace")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def body_of(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if k not in _SKIP}


def body_line(rec: dict) -> str:
    return json.dumps(body_of(rec), separators=(",", ":"), ensure_ascii=False)


def group_key(rec: dict, preset: dict) -> str:
    gb = preset.get("group_by")
    if gb is None:
        return ""
    if isinstance(gb, str):
        value = get(rec, gb)
    else:
        path = gb.get("path")
        value = get(rec, path) if path else None
        rx = gb.get("regex")
        if rx:
            try:
                m = re.search(rx, str(value))
            except re.error:
                m = None
            value = m.group(1) if (m and m.groups()) else None
    return "(none)" if value is None else str(value)


def alert_header(shown: int, total: int) -> str:
    return f"# alerts ({shown} of {total} shown)"


def group_header(key: str, total: int, shown: int) -> str:
    return f"# {key} ({total} records, {shown} shown)"


def _plan(records):
    """Alerts by priority, then non-alert buckets in first-appearance order."""
    alerts = sorted((r for r in records if r["_m"]["alert"]), key=lambda r: r["_m"]["key"])
    rest = sorted((r for r in records if not r["_m"]["alert"]), key=lambda r: r["_m"]["key"])
    buckets: dict[str, list] = {}
    for r in rest:
        buckets.setdefault(r["_m"]["grp"], []).append(r)
    return alerts, buckets


def reduce(records, preset, max_bytes, line_fn=None, reserve=None):
    """Returns (emitted_in_order, reduce_meta). Sets 'shown' on every input record dict."""
    line_fn = line_fn or body_line
    for r in records:
        r["shown"] = False
    meta = {"loss_reason": "budget", "alert_truncated": False,
            "alert_shown": 0, "group_order": [], "groups_shown": 0}
    if not records:
        meta["loss_reason"] = "empty"
        return [], meta

    # A fixed 1024-byte reserve swallows the whole budget at max_bytes <= 1024,
    # leaving nothing for rows — but half of it is too little: real footers
    # measure 648-738 bytes (the 60-char rule alone is 180, "─" being 3 bytes
    # each), so `max_bytes // 2` let alerts overrun 8 of 14 fixtures at 1024.
    # The caller passes the footer's MEASURED size on a second pass; this
    # default only has to be close enough to make that second pass rare.
    if reserve is None:
        reserve = min(FOOTER_RESERVE, max(FOOTER_FLOOR, max_bytes // 2))
    budget = max(0, max_bytes - reserve)
    alerts, buckets = _plan(records)

    # Each record's serialised cost, once. The round robin below revisits a
    # bucket's head on every pass, and re-serialising there is pure waste.
    for r in records:
        r["_m"]["cost"] = bytelen(line_fn(r)) + 1

    # Lossless: does everything, headers included, fit?
    total = bytelen(alert_header(len(alerts), len(alerts))) + 1 if alerts else 0
    total += sum(r["_m"]["cost"] for r in alerts)
    for key, bucket in buckets.items():
        total += bytelen(group_header(key, len(bucket), len(bucket))) + 1
        total += sum(r["_m"]["cost"] for r in bucket)
    if total <= budget:
        emitted = alerts + [r for bucket in buckets.values() for r in bucket]
        for r in emitted:
            r["shown"] = True
        meta.update(loss_reason="lossless", alert_shown=len(alerts),
                    group_order=list(buckets), groups_shown=len(buckets))
        return emitted, meta

    used, emitted = 0, []
    if alerts:
        used += bytelen(alert_header(len(alerts), len(alerts))) + 1
        for r in alerts:
            cost = r["_m"]["cost"]
            if used + cost > budget:
                meta["alert_truncated"] = True
                meta["loss_reason"] = "alert_overflow"
                break  # alerts are already in priority order
            used += cost
            r["shown"] = True
            emitted.append(r)
    meta["alert_shown"] = sum(1 for r in alerts if r["shown"])

    # Rule 1: if alerts alone overflow the budget, non-alerts go to zero first.
    # Filling remaining bytes with low-priority rows while an alert sits withheld
    # is exactly the invariant this renderer exists to prevent.
    if meta["alert_truncated"]:
        meta["group_order"] = []
        meta["groups_shown"] = 0
        return emitted, meta

    # Round robin, one record per bucket per pass: a single noisy group cannot
    # crowd every other group out of the slice.
    opened: list[str] = []
    cursor = {k: 0 for k in buckets}
    progress = True
    while progress:
        progress = False
        for key, bucket in buckets.items():
            i = cursor[key]
            if i >= len(bucket):
                continue
            rec = bucket[i]
            cost = rec["_m"]["cost"]
            if key not in opened:
                cost += bytelen(group_header(key, len(bucket), len(bucket))) + 1
            if used + cost > budget:
                continue  # a smaller record in another bucket may still fit
            used += cost
            cursor[key] = i + 1
            if key not in opened:
                opened.append(key)
            rec["shown"] = True
            emitted.append(rec)
            progress = True

    meta["group_order"] = opened
    meta["groups_shown"] = len(opened)
    return emitted, meta
