#!/usr/bin/env python3
"""Copy vendor/compress/ into a consumer verbatim, stamped and hashed.

    python tools/vendor.py <target-dir>

<target-dir> is the consumer's _compress/ path; it is created if missing. Every
copied file gets the VENDORED header under its module docstring, and VENDOR.json
records a sha256 of the content *before* the header, so re-vendoring is stable.

Refuses to run if the target holds a file that a pre-existing VENDOR.json does
not list — that means someone edited or added something locally.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(ROOT, "vendor", "compress")
MANIFEST = "VENDOR.json"

HEADER = ("# VENDORED from ldm-compress v{version} — do not edit."
          " Re-vendor with tools/vendor.py.")


def version() -> str:
    with open(os.path.join(ROOT, "VERSION")) as fh:
        return fh.read().strip()


def sources(root=SOURCE):
    """Relative paths of every .py under the payload, sorted."""
    out = []
    for dirpath, _dirs, files in os.walk(root):
        for name in sorted(files):
            if name.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dirpath, name), root))
    return sorted(out)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def header_line(ver: str) -> str:
    return HEADER.format(version=ver)


def stamp(text: str, ver: str) -> str:
    """Insert the header directly under the module docstring."""
    line = header_line(ver)
    lines = text.splitlines(keepends=True)
    insert_at = 0
    try:
        tree = ast.parse(text)
        first = tree.body[0] if tree.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            insert_at = first.end_lineno
    except SyntaxError:
        pass
    if insert_at == 0:
        while insert_at < len(lines) and (lines[insert_at].startswith("#!")
                                          or "coding:" in lines[insert_at]):
            insert_at += 1
    lines.insert(insert_at, line + "\n")
    return "".join(lines)


def strip_header(text: str) -> str:
    """Remove the stamped line, whatever version it names, and nothing else."""
    out = [ln for ln in text.splitlines(keepends=True)
           if not (ln.startswith("# VENDORED from ldm-compress")
                   and "do not edit" in ln)]
    return "".join(out)


def load_manifest(target: str) -> dict | None:
    path = os.path.join(target, MANIFEST)
    if not os.path.exists(path):
        return None
    try:
        with open(path) as fh:
            return json.load(fh)
    except ValueError:
        return None


def existing_py(target: str):
    out = []
    for dirpath, _dirs, files in os.walk(target):
        for name in files:
            if name.endswith(".py"):
                out.append(os.path.relpath(os.path.join(dirpath, name), target))
    return sorted(out)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    target = os.path.abspath(argv[0])
    ver = version()

    manifest = load_manifest(target)
    if manifest is not None:
        known = set(manifest.get("files", {}))
        unknown = [p for p in existing_py(target) if p not in known]
        if unknown:
            print(f"vendor: {target} holds files {MANIFEST} does not list — "
                  "someone edited or added something locally:", file=sys.stderr)
            for path in unknown:
                print(f"  {path}", file=sys.stderr)
            return 1

    os.makedirs(target, exist_ok=True)
    wanted = sources()
    hashes = {}
    for rel in wanted:
        raw = open(os.path.join(SOURCE, rel), encoding="utf-8").read()
        hashes[rel] = sha256(raw)          # hash the content before stamping
        dest = os.path.join(target, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(stamp(raw, ver))

    removed = []
    for rel in existing_py(target):
        if rel not in wanted:
            os.remove(os.path.join(target, rel))
            removed.append(rel)
    for dirpath, dirs, files in os.walk(target, topdown=False):
        if not dirs and not files and dirpath != target:
            os.rmdir(dirpath)

    with open(os.path.join(target, MANIFEST), "w") as fh:
        json.dump({"source": "ldm-compress", "version": ver,
                   "vendored_at": datetime.now(timezone.utc)
                   .strftime("%Y-%m-%dT%H:%M:%SZ"),
                   "files": hashes}, fh, indent=1, sort_keys=True)
        fh.write("\n")

    print(f"vendored {len(wanted)} files of ldm-compress v{ver} -> {target}")
    for rel in removed:
        print(f"  removed {rel} (no longer in source)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
