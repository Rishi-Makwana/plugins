"""python -m compress FILE [--preset P] [--max-bytes N] [--json]"""
from __future__ import annotations

import argparse
import json
import sys

from . import render


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="compress", description=__doc__)
    ap.add_argument("file", nargs="?", help="captured output; omit to read stdin")
    ap.add_argument("--preset", default=None)
    ap.add_argument("--max-bytes", type=int, default=None)
    ap.add_argument("--sink", default=None)
    ap.add_argument("--no-store", action="store_true")
    ap.add_argument("--source", default=None)
    ap.add_argument("--json", action="store_true", help="print metadata only")
    a = ap.parse_args(argv)
    try:
        raw = open(a.file, "rb").read() if a.file else sys.stdin.buffer.read()
    except OSError as exc:
        print(f"compress: {exc}", file=sys.stderr)
        return 3
    text, meta = render(raw, preset=a.preset, max_bytes=a.max_bytes, sink=a.sink,
                        store=not a.no_store, source=a.source or a.file)
    if a.json:
        print(json.dumps(meta, indent=2, default=str))
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
