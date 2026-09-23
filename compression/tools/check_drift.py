#!/usr/bin/env python3
"""Has a vendored copy diverged from its manifest?

    python check_drift.py <_compress-dir>     # exit 0 clean, 1 drifted

Consumers run this in CI. It is what stops four copies quietly diverging.
Hashes ignore the stamped VENDORED header, so re-vendoring never shows as drift.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from vendor import MANIFEST, existing_py, load_manifest, sha256, strip_header


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    target = os.path.abspath(argv[0])

    manifest = load_manifest(target)
    if manifest is None:
        print(f"check_drift: no readable {MANIFEST} in {target}", file=sys.stderr)
        return 1
    expected = manifest.get("files", {})

    drifted, missing = [], []
    for rel, want in sorted(expected.items()):
        path = os.path.join(target, rel)
        if not os.path.exists(path):
            missing.append(rel)
            continue
        got = sha256(strip_header(open(path, encoding="utf-8").read()))
        if got != want:
            drifted.append(rel)
    added = [rel for rel in existing_py(target) if rel not in expected]

    if not (drifted or missing or added):
        print(f"clean: {len(expected)} files match ldm-compress "
              f"v{manifest.get('version')}")
        return 0
    for rel in drifted:
        print(f"drifted: {rel}")
    for rel in missing:
        print(f"missing: {rel}")
    for rel in added:
        print(f"added (not vendored): {rel}")
    print(f"check_drift: {len(drifted) + len(missing) + len(added)} file(s) differ "
          f"from {MANIFEST}; re-vendor with tools/vendor.py")
    return 1


if __name__ == "__main__":
    sys.exit(main())
