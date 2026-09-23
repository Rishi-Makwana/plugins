"""JUnit XML. No sniff block — adapters/junit.py decides, from the XML itself."""
PRESET = {
    "name": "junit", "kind": "records",
    "rows_path": ["$"],
    "fields": ["suite", "classname", "name", "status", "time", "message"],
    "field_max_chars": 600,
    "identity": ["classname", "name"],
    "rank": {"path": "status",
             "order": ["error", "failure", "skipped", "passed"], "default": 3},
    "alert": [{"rank_tier": 0}, {"rank_tier": 1}],
    "group_by": "suite",
}
