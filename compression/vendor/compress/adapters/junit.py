"""JUnit XML → flat testcase records."""
from __future__ import annotations

import xml.etree.ElementTree as ET

_OUTCOMES = ("failure", "error", "skipped")
_MESSAGE_MAX = 600


def junit_to_records(xml_text: str) -> list[dict]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    records = []
    suites = [root] if root.tag == "testsuite" else root.iter("testsuite")
    for suite in suites:
        suite_name = suite.get("name") or ""
        for case in suite.iter("testcase"):
            status, message = "passed", None
            for child in case:
                if child.tag in _OUTCOMES:
                    status = child.tag
                    message = child.get("message") or (child.text or "")
                    break
            records.append({
                "suite": suite_name,
                "name": case.get("name") or "",
                "classname": case.get("classname") or "",
                "time": case.get("time"),
                "status": status,
                "message": (message or "")[:_MESSAGE_MAX] or None,
            })
    return records
