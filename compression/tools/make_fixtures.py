#!/usr/bin/env python3
"""Generate tests/fixtures/ deterministically, with a .expect.json beside each one.

    python tools/make_fixtures.py [outdir]

Tail-planted alerts are the whole point: a naive head -N reduction passes every
other check and fails these.
"""
from __future__ import annotations

import json
import os
import random
import sys

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor"))

random.seed(7)

WORDS = ("prototype pollution regular expression denial of service remote code "
         "execution improper input validation path traversal deserialisation "
         "command injection cross site scripting privilege escalation").split()
PKGS = ["lodash", "axios", "minimist", "express", "moment", "handlebars", "jquery",
        "marked", "ws", "yargs", "tar", "glob", "debug", "chalk", "semver",
        "node-fetch", "request", "async", "underscore", "bluebird"]

UNIT_SEP = chr(31)  # store.py joins identity values with this


def blurb(n: int, rnd: random.Random) -> str:
    out = []
    while sum(len(w) + 1 for w in out) < n:
        out.append(rnd.choice(WORDS))
    return " ".join(out)


def write(outdir, name, payload, expect):
    path = os.path.join(outdir, name)
    with open(path, "w") as fh:
        fh.write(payload)
    with open(os.path.join(outdir, name.rsplit(".", 1)[0] + ".expect.json"), "w") as fh:
        json.dump(expect, fh, indent=1, sort_keys=True)
    size = os.path.getsize(path)
    print(f"  {name:<20} {size / 1024:8.1f} KB  {expect.get('total')} records")


# -- snyk ---------------------------------------------------------------------

def snyk_vuln(i, severity, rnd, mature=False, cvss=None, title_len=60):
    pkg = PKGS[i % len(PKGS)]
    return {
        "id": f"SNYK-JS-{pkg.upper()[:6]}-{1000 + i}",
        "packageName": pkg,
        "version": f"{1 + i % 4}.{i % 18}.{i % 7}",
        "severity": severity,
        "cvssScore": cvss if cvss is not None else round(rnd.uniform(1.0, 8.9), 1),
        "exploitMaturity": "mature" if mature else "no-known-exploit",
        "title": blurb(title_len, rnd),
        "fixedIn": [f"{2 + i % 3}.0.0"],
        "CVE": [f"CVE-2024-{2000 + i}"],
        "description": blurb(420, rnd),
        "language": "js",
        "packageManager": "npm",
    }


def make_snyk_200(outdir):
    rnd = random.Random(7)
    plan = ["low"] * 129 + ["medium"] * 60 + ["high"] * 9
    rnd.shuffle(plan)
    plan.insert(118, "critical")
    plan.insert(177, "critical")
    vulns = []
    for i, sev in enumerate(plan):
        if sev == "critical":
            v = snyk_vuln(i, sev, rnd, mature=True, cvss=round(rnd.uniform(9.0, 9.9), 1))
        elif sev == "high":
            # every high is mature, so alerts == 2 criticals + 9 highs == 11
            v = snyk_vuln(i, sev, rnd, mature=True, cvss=round(rnd.uniform(7.0, 8.9), 1))
        else:
            v = snyk_vuln(i, sev, rnd, cvss=round(rnd.uniform(1.0, 6.9), 1))
        vulns.append(v)
    dupes = [json.loads(json.dumps(vulns[i])) for i in range(0, 140, 10)][:14]
    raw = vulns + dupes
    payload = {"ok": False, "packageManager": "npm", "vulnerabilities": raw,
               "summary": f"{len(raw)} vulnerable dependency paths"}
    alerts = [[v["id"], v["packageName"], v["version"]] for v in vulns
              if v["severity"] in ("critical", "high")]
    write(outdir, "snyk-200.json", json.dumps(payload, indent=2), {
        "preset": "snyk", "raw_total": len(raw), "total": len(vulns),
        "by_tier": {"critical": 2, "high": 9, "medium": 60, "low": 129},
        "alert_identities": alerts,
        "planted": [vulns[118]["id"], vulns[177]["id"]]})


def make_snyk_multi(outdir):
    rnd = random.Random(11)
    projects, alerts, tiers = [], [], {}
    idx = 0
    # The point of --all-projects: ONE vulnerability reported once per project,
    # identical every time. Without a real collision here the fixture named for
    # the multi-project case never exercises dedupe at all.
    shared = snyk_vuln(0, "low", rnd, cvss=3.3)
    for p in range(3):
        vulns = []
        for k in range(12):
            if k == 0:
                vulns.append(json.loads(json.dumps(shared)))
                if p == 0:
                    tiers["low"] = tiers.get("low", 0) + 1   # counted once
                continue
            sev = "critical" if (p == 2 and k == 11) else ("medium" if k % 2 else "low")
            cvss = 9.4 if sev == "critical" else round(rnd.uniform(1.0, 6.5), 1)
            v = snyk_vuln(idx + 1, sev, rnd, mature=(sev == "critical"), cvss=cvss)
            vulns.append(v)
            tiers[sev] = tiers.get(sev, 0) + 1
            if sev == "critical":
                alerts.append([v["id"], v["packageName"], v["version"]])
            idx += 1
        projects.append({"ok": False, "packageManager": "npm", "path": f"proj-{p}",
                         "vulnerabilities": vulns, "summary": f"{len(vulns)} paths"})
    write(outdir, "snyk-multi.json", json.dumps(projects, indent=2), {
        "preset": "snyk", "raw_total": 36, "total": 34,   # 3 copies of one vuln -> 1
        "shared_vuln": [shared["id"], shared["packageName"], shared["version"]],
        "by_tier": {k: tiers[k] for k in ("critical", "medium", "low") if k in tiers},
        "alert_identities": alerts})


# -- sarif --------------------------------------------------------------------

def make_sarif_140(outdir):
    rnd = random.Random(13)
    plan = ["error"] * 2 + ["warning"] * 40 + ["note"] * 97
    rnd.shuffle(plan)
    plan.insert(131, "error")
    results, alerts = [], []
    for i, level in enumerate(plan):
        uri = f"src/{'abcdefgh'[i % 8]}/module_{i % 14}.py"
        sev = 8.4 if level == "error" else round(rnd.uniform(0.5, 6.5), 1)
        rule = f"py/{'-'.join(rnd.sample(WORDS, 2))}-{i % 25}"
        results.append({
            "ruleId": rule, "level": level,
            "message": {"text": blurb(90, rnd)},
            "locations": [{"physicalLocation": {
                "artifactLocation": {"uri": uri},
                "region": {"startLine": 10 + i, "startColumn": 1 + i % 40}}}],
            "properties": {"security-severity": str(sev), "tags": ["security"]},
            "partialFingerprints": {"primaryLocationLineHash": f"{i:08x}"},
        })
        if level == "error" or sev >= 7.0:
            alerts.append([rule, uri, 10 + i])
    payload = {"$schema": "https://json.schemastore.org/sarif-2.1.0.json",
               "version": "2.1.0",
               "runs": [{"tool": {"driver": {"name": "CodeQL", "version": "2.16.0"}},
                         "results": results}]}
    write(outdir, "sarif-140.json", json.dumps(payload, indent=2), {
        "preset": "sarif", "raw_total": 140, "total": 140,
        "by_tier": {"error": 3, "warning": 40, "note": 97},
        "alert_identities": alerts, "planted": [results[131]["ruleId"]]})


# -- sonarqube ----------------------------------------------------------------

def make_sonar_20(outdir):
    rnd = random.Random(17)
    plan = ["blocker", "critical", "critical", "major", "major", "major", "major",
            "major", "major", "major", "major", "minor", "minor", "minor", "minor",
            "minor", "minor", "minor", "minor", "blocker"]
    issues, alerts = [], []
    for i, sev in enumerate(plan):
        key = f"AY{i:05d}-{rnd.randrange(1000, 9999)}"
        kind = "VULNERABILITY" if sev in ("blocker", "critical") else "CODE_SMELL"
        issues.append({"key": key, "rule": f"java:S{1000 + i}", "severity": sev.upper(),
                       "type": kind, "component": f"app:src/main/Class{i % 6}.java",
                       "line": 20 + i, "message": blurb(120, rnd), "status": "OPEN",
                       "effort": f"{5 + i}min", "author": "dev@example.com"})
        if sev in ("blocker", "critical"):
            alerts.append([key])
    payload = {"total": 20, "p": 1, "ps": 100,
               "paging": {"pageIndex": 1, "pageSize": 100, "total": 20},
               "issues": issues}
    write(outdir, "sonar-20.json", json.dumps(payload, indent=2), {
        "preset": "sonarqube", "raw_total": 20, "total": 20,
        "by_tier": {"blocker": 2, "critical": 2, "major": 8, "minor": 8},
        "alert_identities": alerts, "planted": [issues[19]["key"]]})


# -- dependabot ---------------------------------------------------------------

def make_dependabot_6(outdir):
    rnd = random.Random(19)
    plan = ["critical", "high", "high", "high", "high", "critical"]
    rows, alerts = [], []
    for i, sev in enumerate(plan):
        pkg = PKGS[i]
        rows.append({
            "number": 100 + i, "state": "open",
            "security_advisory": {"severity": sev, "summary": blurb(140, rnd),
                                  "cvss": {"score": 9.8 if sev == "critical" else 7.5,
                                           "vector_string": "CVSS:3.1/AV:N"},
                                  "ghsa_id": f"GHSA-{i:04d}-xxxx-yyyy"},
            "security_vulnerability": {
                "package": {"name": pkg, "ecosystem": "npm"},
                "first_patched_version": {"identifier": f"{2 + i}.0.1"},
                "vulnerable_version_range": "< 2.0.1"},
            "dependency": {"manifest_path": f"apps/{i % 2}/package.json",
                           "scope": "runtime"},
            "html_url": f"https://github.com/o/r/security/dependabot/{100 + i}",
        })
        if sev == "critical":
            alerts.append([100 + i])
    write(outdir, "dependabot-6.json", json.dumps(rows, indent=2), {
        "preset": "dependabot", "raw_total": 6, "total": 6,
        "by_tier": {"critical": 2, "high": 4},
        "alert_identities": alerts, "planted": [105]})


# -- trivy --------------------------------------------------------------------

def make_trivy_80(outdir):
    rnd = random.Random(23)
    plan = ["HIGH"] * 12 + ["MEDIUM"] * 67
    rnd.shuffle(plan)
    plan.append("CRITICAL")           # flattened index 79, inside Results[2]
    results, alerts, idx = [], [], 0
    for r, count in enumerate((30, 30, 20)):
        vulns = []
        for _ in range(count):
            sev = plan[idx]
            vid = f"CVE-2024-{3000 + idx}"
            pkg = PKGS[idx % len(PKGS)]
            score = 9.6 if sev == "CRITICAL" else round(rnd.uniform(3.0, 8.5), 1)
            vulns.append({"VulnerabilityID": vid, "PkgName": pkg,
                          "InstalledVersion": f"1.{idx % 9}.0",
                          "FixedVersion": f"1.{idx % 9}.1", "Severity": sev,
                          "CVSS": {"nvd": {"V3Score": score, "V3Vector": "AV:N"}},
                          "Title": blurb(90, rnd), "Description": blurb(300, rnd)})
            if sev == "CRITICAL" or score >= 9.0:
                alerts.append([vid, pkg, f"1.{idx % 9}.0"])
            idx += 1
        results.append({"Target": f"layer-{r}", "Class": "os-pkgs", "Type": "debian",
                        "Vulnerabilities": vulns})
    payload = {"SchemaVersion": 2, "ArtifactName": "app:latest",
               "ArtifactType": "container_image", "Results": results}
    write(outdir, "trivy-80.json", json.dumps(payload, indent=2), {
        "preset": "trivy", "raw_total": 80, "total": 80,
        "by_tier": {"critical": 1, "high": 12, "medium": 67},
        "alert_identities": alerts,
        "planted": [results[2]["Vulnerabilities"][-1]["VulnerabilityID"]]})


# -- gh runs ------------------------------------------------------------------

def make_gh_runs_50(outdir):
    rnd = random.Random(29)
    rows, alerts = [], []
    for i in range(50):
        concl = "failure" if i in (0, 25, 49) else "success"
        rows.append({"databaseId": 900000 + i, "workflowName": f"ci-{i % 4}",
                     "conclusion": concl, "status": "completed",
                     "headBranch": rnd.choice(["main", "dev", "release"]),
                     "event": "push", "createdAt": f"2026-09-{1 + i % 28:02d}T10:00:00Z",
                     "url": f"https://github.com/o/r/actions/runs/{900000 + i}",
                     "displayTitle": blurb(60, rnd)})
        if concl == "failure":
            alerts.append([900000 + i])
    write(outdir, "gh-runs-50.json", json.dumps(rows, indent=2), {
        "preset": "gh_runs", "raw_total": 50, "total": 50,
        "by_tier": {"failure": 3, "success": 47},
        "alert_identities": alerts, "planted": [900000, 900025, 900049]})


# -- pytest -------------------------------------------------------------------

def make_pytest_300(outdir):
    rnd = random.Random(31)
    tests, alerts = [], []
    for i in range(300):
        failed = i in (12, 150, 298, 299)
        nodeid = f"tests/test_mod_{i % 12}.py::test_case_{i}"
        rec = {"nodeid": nodeid, "outcome": "failed" if failed else "passed",
               "duration": round(rnd.uniform(0.001, 2.5), 4),
               "setup": {"outcome": "passed", "duration": 0.001}}
        if failed:
            rec["call"] = {"outcome": "failed", "duration": 0.2,
                           "crash": {"message": "AssertionError: " + blurb(18, rnd),
                                     "path": nodeid.split("::")[0], "lineno": 40 + i},
                           "longrepr": blurb(40, rnd)}
            alerts.append([nodeid])
        else:
            rec["call"] = {"outcome": "passed", "duration": 0.01}
        tests.append(rec)
    payload = {"created": 1758500000.0, "duration": 12.5, "exitcode": 1,
               "root": "/repo", "summary": {"passed": 296, "failed": 4, "total": 300},
               "tests": tests}
    write(outdir, "pytest-300.json", json.dumps(payload, indent=2), {
        "preset": "pytest_json", "raw_total": 300, "total": 300,
        "by_tier": {"failed": 4, "passed": 296},
        "alert_identities": alerts,
        "planted": [tests[i]["nodeid"] for i in (12, 150, 298, 299)]})


# -- junit --------------------------------------------------------------------

def make_junit_120(outdir):
    from xml.sax.saxutils import escape, quoteattr
    rnd = random.Random(37)
    plan = ["passed"] * 120
    plan[7] = plan[63] = "failure"
    plan[119] = "error"
    out = ['<?xml version="1.0" encoding="UTF-8"?>', "<testsuites>"]
    alerts = []
    per_suite = 40
    for s in range(3):
        out.append(f'  <testsuite name="suite-{s}" tests="{per_suite}">')
        for k in range(per_suite):
            i = s * per_suite + k
            status = plan[i]
            cls, name = f"pkg.Class{i % 9}", f"test_{i:03d}"
            attrs = (f"classname={quoteattr(cls)} name={quoteattr(name)} "
                     f'time="{rnd.uniform(0.01, 3):.3f}"')
            if status == "passed":
                out.append(f"    <testcase {attrs}/>")
            else:
                msg = escape(blurb(120, rnd))
                out.append(f"    <testcase {attrs}>")
                out.append(f'      <{status} message="{msg}">'
                           f"{escape(blurb(300, rnd))}</{status}>")
                out.append("    </testcase>")
                alerts.append([cls, name])
        out.append("  </testsuite>")
    out.append("</testsuites>")
    write(outdir, "junit-120.xml", "\n".join(out), {
        "preset": "junit", "raw_total": 120, "total": 120,
        "by_tier": {"error": 1, "failure": 2, "passed": 117},
        "alert_identities": alerts,
        "planted": ["test_119"]})


# -- logs ---------------------------------------------------------------------

def make_gha_log(outdir):
    rnd = random.Random(41)
    comps = ["build", "test", "lint", "deploy", "cache", "setup"]
    lines, alerts, planted = [], [], []
    # Lines stay short: an alert carries 3 lines of context either side, and all 21
    # of those must survive the 1024-byte body budget at max_bytes 2048.
    for n in range(1, 9001):
        comp = comps[n % len(comps)]
        ts = f"{n // 3600 % 24:02d}:{n // 60 % 60:02d}:{n % 60:02d}"
        if n in (4102, 8873, 8991):
            body = f"[{comp}] ERROR fatal ERR-{n}"
            alerts.append([str(n)])
            planted.append(f"ERR-{n}")
        elif n % 97 == 0:
            body = f"[{comp}] warning: {blurb(14, rnd)}"
        else:
            body = f"[{comp}] info: {blurb(14, rnd)}"
        lines.append(f"{ts} {body}")
    write(outdir, "gha-log-9k.log", "\n".join(lines) + "\n", {
        "preset": "logs", "raw_total": 9000, "total": 9000,
        "by_tier": None, "alert_identities": alerts, "planted": planted})


# -- edge cases ---------------------------------------------------------------

def make_alert_overflow(outdir):
    rnd = random.Random(43)
    vulns, alerts = [], []
    for i in range(500):
        v = snyk_vuln(i, "critical", rnd, mature=True, cvss=9.5, title_len=40)
        v["description"] = blurb(80, rnd)
        vulns.append(v)
        alerts.append([v["id"], v["packageName"], v["version"]])
    payload = {"ok": False, "packageManager": "npm", "vulnerabilities": vulns,
               "summary": "500 vulnerable dependency paths"}
    write(outdir, "alert-overflow.json", json.dumps(payload, indent=2), {
        "preset": "snyk", "raw_total": 500, "total": 500,
        "by_tier": {"critical": 500}, "alert_identities": alerts,
        "expect_alert_truncated": True})


def make_unknown_json(outdir):
    rnd = random.Random(47)
    events, alerts = [], []
    for i in range(120):
        level = "error" if i % 5 == 0 else ("warn" if i % 3 == 0 else "info")
        eid = f"evt-{i:04d}"
        events.append({"id": eid, "level": level, "component": f"svc-{i % 7}",
                       "msg": blurb(140, rnd), "at": f"2026-09-22T10:{i % 60:02d}:00Z",
                       "host": f"node-{i % 5}"})
        if level == "error":
            alerts.append([eid])
    payload = {"generated": "2026-09-22", "events": events}
    write(outdir, "unknown-json.json", json.dumps(payload, indent=2), {
        "preset": None, "raw_total": 120, "total": 120,
        "by_tier": {"error": 24, "warn": 32, "info": 64},
        "alert_identities": alerts})


def make_empty(outdir):
    payload = {"ok": True, "packageManager": "npm", "vulnerabilities": [],
               "summary": "no vulnerable paths found"}
    write(outdir, "empty.json", json.dumps(payload, indent=2), {
        "preset": "snyk", "raw_total": 0, "total": 0, "by_tier": {},
        "alert_identities": [], "loss_reason": "empty"})


def make_deep_trunc(outdir):
    rnd = random.Random(53)
    token = "zylophonic-marmoset-7734"
    vulns, alerts = [], []
    for i in range(40):
        v = snyk_vuln(i, "medium" if i else "critical", rnd,
                      cvss=9.9 if i == 0 else 4.0)
        filler = blurb(1000, rnd)
        v["title"] = (filler[:699] + " " + token + " " + filler[700:900])[:900]
        vulns.append(v)
        if i == 0:
            alerts.append([v["id"], v["packageName"], v["version"]])
    payload = {"ok": False, "packageManager": "npm", "vulnerabilities": vulns,
               "summary": "40 vulnerable dependency paths"}
    write(outdir, "deep-trunc.json", json.dumps(payload, indent=2), {
        "preset": "snyk", "raw_total": 40, "total": 40,
        "by_tier": {"critical": 1, "medium": 39},
        "alert_identities": alerts, "search_token": token})


BUILDERS = [make_snyk_200, make_snyk_multi, make_sarif_140, make_sonar_20,
            make_dependabot_6, make_trivy_80, make_gh_runs_50, make_pytest_300,
            make_junit_120, make_gha_log, make_alert_overflow, make_unknown_json,
            make_empty, make_deep_trunc]


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    outdir = argv[0] if argv else os.path.join(root, "tests", "fixtures")
    os.makedirs(outdir, exist_ok=True)
    print(f"fixtures -> {outdir}")
    for builder in BUILDERS:
        builder(outdir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
