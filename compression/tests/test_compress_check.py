"""compress_check.py — the triage tool that says whether to compress at all.

It is unvendored and lives at the repo root. The shape it reports drives the
verdict, and two shapes mean "don't compress this": prose and source code are
out of scope per spec 10 (no rows to rank).
"""
import json
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
CHECK = ROOT / "compress_check.py"
FIXTURES = ROOT / "tests" / "fixtures"

USE, SKIP, BORDERLINE, ERROR = 0, 1, 2, 3


def run(path):
    """→ (exit_code, shape). --json so the shape is machine-readable."""
    proc = subprocess.run([sys.executable, str(CHECK), str(path), "--json"],
                          capture_output=True, text=True)
    shape = json.loads(proc.stdout)["shape"] if proc.stdout.lstrip().startswith("{") else None
    return proc.returncode, shape


def write(tmp_path, name, text):
    p = tmp_path / name
    p.write_text(text, encoding="utf-8")
    return p


# -- the two out-of-scope shapes must SKIP -----------------------------------

def test_a_markdown_spec_is_prose_not_log_lines():
    """A document ABOUT error handling used to score 6.9% on the "contains the
    word error" test and read as a log. Structure, not vocabulary, decides."""
    spec = pathlib.Path("/Users/rishimakwana/Downloads/ldm-compress-BUILD.md")
    if not spec.exists():
        pytest.skip("build spec not present")
    code, shape = run(spec)
    assert shape == "prose" and code == SKIP


def test_generated_markdown_is_prose(tmp_path):
    body = ["# Design", "", "The renderer must never hide a record.", ""]
    for i in range(300):
        body += [f"## Section {i}", "",
                 "- an error here is handled by the caller",
                 "- failure modes are listed below", "",
                 "This paragraph explains the rule in ordinary sentences.", ""]
    code, shape = run(write(tmp_path, "doc.md", "\n".join(body)))
    assert shape == "prose" and code == SKIP


def test_python_source_is_source_code(tmp_path):
    """CODE_RE matches keywords only and scores real Python at 0.23 — under its
    own 0.3 bar — because docstrings and assignments are most of a file."""
    src = "\n".join(str(p.read_text(encoding="utf-8"))
                    for p in sorted((ROOT / "vendor" / "compress").glob("*.py")))
    code, shape = run(write(tmp_path, "src.py", src))
    assert shape == "source code" and code == SKIP


# -- what must still be compressed -------------------------------------------

def test_a_real_log_is_still_log_lines_and_worth_compressing():
    code, shape = run(FIXTURES / "gha-log-9k.log")
    assert shape == "log lines" and code == USE


def test_stack_traces_stay_log_lines(tmp_path):
    """Indented and sentence-free like source, but this is output, not code —
    it is exactly what the renderer exists to compress."""
    one = ('Traceback (most recent call last):\n'
           '  File "/app/svc/handler.py", line 214, in process\n'
           '    result = self.backend.dispatch(payload)\n'
           'ConnectionError: upstream refused\n')
    code, shape = run(write(tmp_path, "t.log", one * 300))
    assert shape == "log lines"
    assert code in (USE, BORDERLINE)


@pytest.mark.parametrize("name,want_code", [
    ("snyk-200.json", USE),
    ("sarif-140.json", USE),
    ("unknown-json.json", USE),
    ("dependabot-6.json", SKIP),      # under the lossless threshold
    ("empty.json", SKIP),
])
def test_json_fixtures_keep_their_verdicts(name, want_code):
    code, shape = run(FIXTURES / name)
    assert code == want_code
    assert shape.startswith("json")


def test_large_json_reports_the_preset_it_would_use():
    proc = subprocess.run([sys.executable, str(CHECK), str(FIXTURES / "snyk-200.json"),
                           "--json"], capture_output=True, text=True)
    payload = json.loads(proc.stdout)
    assert payload["preset"] == "snyk"
    assert payload["records"] == 214
    assert payload["rank_field"] == "severity"
    assert payload["verdict"] == "USE"


# -- the contract the build depends on ---------------------------------------

def test_lexicons_still_match_the_vendored_copy():
    """3.5 step 3: autodetect.py's table is copied from here, not retyped."""
    sys.path.insert(0, str(ROOT / "vendor"))
    from compress.autodetect import LEXICONS as vendored
    import re
    block = re.search(r"^LEXICONS = \[.*?^\]", CHECK.read_text(), re.S | re.M).group(0)
    ns: dict = {}
    exec(block, ns)
    assert ns["LEXICONS"] == vendored


def test_it_never_raises_on_junk(tmp_path):
    for name, body in [("empty.txt", ""), ("nul.txt", "\x00\x01\x02"),
                       ("one.txt", "x"), ("wide.txt", "漏洞" * 5000)]:
        code, _shape = run(write(tmp_path, name, body))
        assert code in (USE, SKIP, BORDERLINE, ERROR)


# -- the indentation signal must generalise past Python, and must NOT catch
# -- indented OUTPUT (which is exactly what should be compressed) ------------

def test_brace_languages_are_source_code(tmp_path):
    js = []
    for i in range(300):
        js += [f"function handler{i}(req, res) {{", "  const data = req.body.items;",
               "  if (!data) {", "    return res.status(400).send('bad');", "  }",
               "  return res.json({ ok: true });", "}", ""]
    code, shape = run(write(tmp_path, "src.js", "\n".join(js)))
    assert shape == "source code" and code == SKIP


def test_indented_tree_output_is_not_mistaken_for_source(tmp_path):
    """npm ls / ls -R are indented and sentence-free, but they are OUTPUT."""
    tree = []
    for i in range(500):
        tree += [f"app@1.0.0 /repo/pkg{i}", "├── lodash@4.17.21",
                 "│   └── deps@1.2.3", "└── axios@0.21.1",
                 "    └── follow-redirects@1.14.0"]
    code, shape = run(write(tmp_path, "npmls.txt", "\n".join(tree)))
    assert shape == "log lines" and code == USE


def test_restructuredtext_is_prose(tmp_path):
    """rst marks headings with underlines, which have no prefix to match."""
    rst = ["Design Notes", "============", ""]
    for i in range(300):
        rst += [f"Section {i}", "-" * 12, "",
                "The renderer must never hide a record from the reader.",
                "An error here is reported to the caller.", ""]
    code, shape = run(write(tmp_path, "doc.rst", "\n".join(rst)))
    assert shape == "prose" and code == SKIP
