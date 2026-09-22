"""Trivy — `trivy image|fs --format json`."""
PRESET = {
    "name": "trivy", "kind": "records",
    "sniff": {"all": [{"path": "Results", "exists": True},
                      {"path": "SchemaVersion", "exists": True}]},
    "rows_path": ["Results[].Vulnerabilities"],
    "fields": ["VulnerabilityID", "PkgName", "InstalledVersion", "FixedVersion", "Severity",
               "CVSS.nvd.V3Score", "Title"],
    "identity": ["VulnerabilityID", "PkgName", "InstalledVersion"],
    "rank": {"path": "Severity",
             "order": ["critical", "high", "medium", "low", "unknown"], "default": 4},
    "score": {"path": "CVSS.nvd.V3Score", "desc": True},
    "alert": [{"rank_tier": 0}, {"path": "CVSS.nvd.V3Score", "gte": 9.0}],
    "group_by": "PkgName",
}
