"""Plain log lines. Tiers come from regex_order, not a field."""
PRESET = {
    "name": "logs", "kind": "lines",
    "rank": {"regex_order": [
        r"(?i)\b(fatal|panic|error|exception|traceback|FAIL(ED)?|✗)\b|##\[error\]",
        r"(?i)\b(warn(ing)?|deprecated)\b|##\[warning\]",
        r"(?i)\b(info|notice)\b"], "default": 3,
        # labels are not in the spec; the footer needs a name per tier (§5.2).
        "labels": ["error", "warn", "info"]},
    "alert": [{"rank_tier": 0}],
    "context_lines": 3,
    "group_by": {"regex": r"^\[?([A-Za-z][\w.-]*)"},
    "fields": ["line"], "max_bytes": 12288,
}
