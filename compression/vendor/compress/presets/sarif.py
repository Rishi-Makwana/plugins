"""SARIF 2.1.0 — covers CodeQL, Semgrep, ESLint, Bandit, Checkov, tfsec, Gitleaks."""
PRESET = {
    "name": "sarif", "kind": "records",
    "sniff": {"any": [{"path": "$schema", "matches": "(?i)sarif"},
                      {"path": "runs[0].tool", "exists": True}]},
    "rows_path": ["runs[].results"],
    "fields": ["ruleId", "level", "message.text",
               "locations[0].physicalLocation.artifactLocation.uri",
               "locations[0].physicalLocation.region.startLine",
               "properties.security-severity"],
    "identity": ["ruleId", "locations[0].physicalLocation.artifactLocation.uri",
                 "locations[0].physicalLocation.region.startLine"],
    # default 1: the SARIF spec says a missing `level` means `warning`.
    "rank": {"path": "level", "order": ["error", "warning", "note", "none"], "default": 1},
    "score": {"path": "properties.security-severity", "desc": True},
    "alert": [{"rank_tier": 0}, {"path": "properties.security-severity", "gte": 7.0}],
    "group_by": "locations[0].physicalLocation.artifactLocation.uri",
}
