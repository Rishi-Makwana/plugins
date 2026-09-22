"""Rank tier, alert flag and sort key for one record."""
from __future__ import annotations

from .paths import get
from .predicates import evaluate


def ident_part(value) -> str:
    """One identity field, canonicalised.

    The dedupe key and the stored `ident` must agree, or `fetch --ids` points
    at a different record than the one dedupe kept. Tools are inconsistent
    about JSON number types — 9 vs 9.0 vs "9.0" for the same finding — so fold
    those together, while keeping True distinct from 1 (Python makes them equal
    and equally hashable; they are not the same identity).
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def ident_of(record, identity, getter) -> str | None:
    if not identity:
        return None
    return "\x1f".join(ident_part(getter(record, p)) for p in identity)


def tier_of(record, rank_cfg) -> tuple[int, str]:
    rank_cfg = rank_cfg or {}
    order = [str(x).strip().lower() for x in rank_cfg.get("order") or []]
    default = rank_cfg.get("default", len(order))
    path = rank_cfg.get("path")
    if not path or not order:
        return default, "other"
    value = str(get(record, path)).strip().lower()
    if value in order:
        i = order.index(value)
        return i, order[i]
    return default, "other"


def is_alert(record, alert_cfg, tier) -> bool:
    if not alert_cfg:
        return tier == 0
    return any(evaluate(record, pred, tier) for pred in alert_cfg)


def sort_key(record, tier, score_cfg, index) -> tuple:
    raw = 0.0
    if score_cfg and score_cfg.get("path"):
        try:
            raw = float(get(record, score_cfg["path"]) or 0)
        except (TypeError, ValueError):
            raw = 0.0
    score = -raw if (score_cfg or {}).get("desc") else raw
    return (tier, score, index)
