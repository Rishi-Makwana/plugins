"""The preset table and the sniffer that picks one from a payload."""
from __future__ import annotations

from ..predicates import evaluate
from .dependabot import PRESET as DEPENDABOT
from .gh_runs import PRESET as GH_RUNS
from .junit import PRESET as JUNIT
from .logs import PRESET as LOGS
from .pytest_json import PRESET as PYTEST_JSON
from .sarif import PRESET as SARIF
from .snyk import PRESET as SNYK
from .sonarqube import PRESET as SONARQUBE
from .trivy import PRESET as TRIVY

# Order matters: sarif first, then the narrowest sniff blocks.
_ORDERED = [SARIF, SNYK, SONARQUBE, TRIVY, PYTEST_JSON, DEPENDABOT, GH_RUNS, JUNIT, LOGS]

PRESETS: dict[str, dict] = {p["name"]: p for p in _ORDERED}


def sniff(payload) -> str | None:
    """First preset whose sniff block matches the payload root; else None."""
    for preset in _ORDERED:
        block = preset.get("sniff")
        if not block:
            continue
        for mode in ("all", "any"):
            preds = block.get(mode)
            if not preds:
                continue
            hits = [evaluate(payload, p, -1) for p in preds]
            if (all(hits) if mode == "all" else any(hits)):
                return preset["name"]
    return None
