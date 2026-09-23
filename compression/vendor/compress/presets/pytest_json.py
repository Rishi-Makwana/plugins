"""pytest --json-report. field_max_chars is raised because longrepr is the traceback."""
PRESET = {
    "name": "pytest_json", "kind": "records",
    "sniff": {"all": [{"path": "tests", "exists": True}, {"path": "summary", "exists": True}]},
    "rows_path": ["tests"],
    "fields": ["nodeid", "outcome", "duration", "call.crash.message", "call.longrepr"],
    "field_max_chars": 600,
    "identity": ["nodeid"],
    "rank": {"path": "outcome",
             "order": ["failed", "error", "xpassed", "skipped", "xfailed", "passed"],
             "default": 5},
    "alert": [{"rank_tier": 0}, {"rank_tier": 1}],
    "group_by": {"path": "nodeid", "regex": r"^([^:]+)"},
}
