"""SonarQube — GET /api/issues/search."""
PRESET = {
    "name": "sonarqube", "kind": "records",
    "sniff": {"all": [{"path": "issues", "exists": True}, {"path": "paging", "exists": True}]},
    "rows_path": ["issues"],
    "fields": ["key", "rule", "severity", "type", "component", "line", "message",
               "status", "effort"],
    "identity": ["key"],
    "rank": {"path": "severity",
             "order": ["blocker", "critical", "major", "minor", "info"], "default": 2},
    "alert": [{"rank_tier": 0}, {"rank_tier": 1}, {"path": "type", "eq": "VULNERABILITY"}],
    "group_by": "component",
}
