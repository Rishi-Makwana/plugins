"""Vendoring: a verbatim copy, stamped, hashed, and checkable."""
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_drift  # noqa: E402
import vendor  # noqa: E402


@pytest.fixture
def vendored(tmp_path):
    target = str(tmp_path / "_compress")
    assert vendor.main([target]) == 0
    return target


def test_every_payload_file_is_copied(vendored):
    manifest = json.load(open(os.path.join(vendored, "VENDOR.json")))
    assert manifest["source"] == "ldm-compress"
    assert manifest["version"] == vendor.version()
    assert set(manifest["files"]) == set(vendor.sources())
    for rel in manifest["files"]:
        assert os.path.exists(os.path.join(vendored, rel))


def test_header_sits_under_the_module_docstring(vendored):
    lines = open(os.path.join(vendored, "paths.py")).read().splitlines()
    assert lines[0].startswith('"""')
    assert lines[1] == vendor.header_line(vendor.version())


def test_header_goes_first_when_there_is_no_docstring(tmp_path):
    stamped = vendor.stamp("import os\n", "9.9.9")
    assert stamped.splitlines()[0] == vendor.header_line("9.9.9")
    shebang = vendor.stamp("#!/usr/bin/env python3\nimport os\n", "9.9.9")
    assert shebang.splitlines()[1] == vendor.header_line("9.9.9")


def test_manifest_hashes_the_content_before_stamping(vendored):
    manifest = json.load(open(os.path.join(vendored, "VENDOR.json")))
    raw = open(os.path.join(ROOT, "vendor", "compress", "paths.py")).read()
    assert manifest["files"]["paths.py"] == vendor.sha256(raw)


def test_drift_check_is_clean_and_stays_clean_on_re_vendor(vendored, capsys):
    assert check_drift.main([vendored]) == 0
    assert vendor.main([vendored]) == 0
    assert check_drift.main([vendored]) == 0
    assert "clean" in capsys.readouterr().out


def test_an_edit_shows_up_as_drift(vendored, capsys):
    with open(os.path.join(vendored, "reduce.py"), "a") as fh:
        fh.write("\n# local edit\n")
    assert check_drift.main([vendored]) == 1
    assert "drifted: reduce.py" in capsys.readouterr().out


def test_a_deleted_file_shows_up_as_missing(vendored, capsys):
    os.remove(os.path.join(vendored, "presets", "trivy.py"))
    assert check_drift.main([vendored]) == 1
    assert "missing: presets/trivy.py" in capsys.readouterr().out


def test_vendor_refuses_an_unlisted_file(vendored, capsys):
    with open(os.path.join(vendored, "rogue.py"), "w") as fh:
        fh.write("x = 1\n")
    assert vendor.main([vendored]) == 1
    assert "rogue.py" in capsys.readouterr().err


def test_files_removed_from_source_are_deleted_from_the_target(vendored, capsys):
    stale = os.path.join(vendored, "stale.py")
    with open(stale, "w") as fh:
        fh.write("x = 1\n")
    manifest_path = os.path.join(vendored, "VENDOR.json")
    manifest = json.load(open(manifest_path))
    manifest["files"]["stale.py"] = "0" * 64
    json.dump(manifest, open(manifest_path, "w"))
    assert vendor.main([vendored]) == 0
    assert not os.path.exists(stale)
    assert "removed stale.py" in capsys.readouterr().out


def test_the_vendored_copy_runs_under_a_consumer_package_name(tmp_path):
    pkg = tmp_path / "src" / "ldm_appsec"
    assert vendor.main([str(pkg / "_compress")]) == 0
    (pkg / "__init__.py").write_text("")
    script = tmp_path / "call.py"
    script.write_text(
        "from ldm_appsec._compress import render, render_lines, fetch, sniff, PRESETS\n"
        "text, meta = render({'ok': False, 'packageManager': 'npm',"
        " 'vulnerabilities': [{'id': 'A', 'packageName': 'p', 'version': '1',"
        " 'severity': 'critical'}]}, preset='snyk', store=False)\n"
        "assert meta['total'] == 1 and meta['alert_count'] == 1\n"
        "assert sorted(PRESETS)[0] == 'dependabot'\n"
        "print('ok')\n")
    env = dict(os.environ, PYTHONPATH=str(tmp_path / "src"),
               LDM_COMPRESS_DIR=str(tmp_path / "out"))
    out = subprocess.run([sys.executable, str(script)], env=env,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_the_payload_imports_nothing_third_party():
    allowed = {"argparse", "ast", "collections", "datetime", "errno", "hashlib",
               "json", "os", "random", "re", "sqlite3", "statistics", "sys",
               "tempfile", "time", "xml", "typing", "__future__", "importlib",
               "itertools", "math", "pathlib", "functools"}
    import ast as ast_mod
    offenders = []
    for rel in vendor.sources():
        tree = ast_mod.parse(open(os.path.join(ROOT, "vendor", "compress", rel)).read())
        for node in ast_mod.walk(tree):
            if isinstance(node, ast_mod.Import):
                for alias in node.names:
                    if alias.name.split(".")[0] not in allowed:
                        offenders.append((rel, alias.name))
            elif isinstance(node, ast_mod.ImportFrom):
                if node.level == 0 and (node.module or "").split(".")[0] not in allowed:
                    offenders.append((rel, node.module))
    assert offenders == []
