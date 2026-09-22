"""Dependabot — gh api /repos/{o}/{r}/dependabot/alerts --paginate."""
PRESET = {
    "name": "dependabot", "kind": "records",
    "sniff": {"any": [{"path": "[0].security_advisory", "exists": True}]},
    "rows_path": ["$"],
    "fields": ["number", "state", "security_advisory.severity", "security_advisory.cvss.score",
               "security_vulnerability.package.name", "security_vulnerability.package.ecosystem",
               "security_vulnerability.first_patched_version.identifier",
               "dependency.manifest_path", "security_advisory.summary"],
    "identity": ["number"],
    "rank": {"path": "security_advisory.severity",
             "order": ["critical", "high", "medium", "low"], "default": 3},
    "score": {"path": "security_advisory.cvss.score", "desc": True},
    "alert": [{"rank_tier": 0}, {"path": "security_advisory.cvss.score", "gte": 9.0}],
    "group_by": "security_vulnerability.package.name",
}
