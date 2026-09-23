"""GitHub Actions runs — gh run list --json databaseId,workflowName,conclusion,..."""
PRESET = {
    "name": "gh_runs", "kind": "records",
    "sniff": {"all": [{"path": "[0].conclusion", "exists": True},
                      {"path": "[0].workflowName", "exists": True}]},
    "rows_path": ["$", "check_runs", "workflow_runs"],
    "fields": ["databaseId", "workflowName", "conclusion", "status", "headBranch",
               "event", "createdAt", "url"],
    "identity": ["databaseId"],
    "rank": {"path": "conclusion",
             "order": ["failure", "startup_failure", "timed_out", "cancelled",
                       "action_required", "neutral", "skipped", "success"], "default": 5},
    "alert": [{"rank_tier": 0}, {"rank_tier": 1}, {"rank_tier": 2}],
    "group_by": "workflowName",
}
