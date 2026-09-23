#!/usr/bin/env python3
"""Loss harness. Not vendored — this stays in the source repo.

    python tools/measure.py tests/fixtures --budgets 1024,4096,12288

Hard gates (exit 1 on any failure):
  count_accuracy == 1.0   footer totals equal an independent recount of the fixture
  tier_presence  == 1.0   every non-empty tier is named in the footer
  silent_loss    == 0     shown + withheld == total, and withheld is stated
  alert_recall   == 1.0   every budget: all alerts shown, OR alert_truncated set
                          with the exact withheld count printed in the footer
  alert_truncated is False at the full budget, unless the fixture expects otherwise

alert_recall is gated at every budget. "Recall is always 1.0" is unsatisfiable at
a small enough budget (the alerts alone do not fit), but 0 rule 1's actual
requirement — if alerts are cut, say exactly how many — always is. Scoping this
gate to 12288 previously hid a real 0.50 recall on unknown-json at 4096.

row_recall and reduction are reported, never gated: 200 -> 22 is 0.11 on purpose.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "vendor"))

import compress                                                    # noqa: E402
from compress.footer import RULE                                   # noqa: E402
from compress.reduce import bytelen                                # noqa: E402

FULL_BUDGET = 12288


def fixtures(directory):
    for name in sorted(os.listdir(directory)):
        if name.endswith(".expect.json"):
            continue
        path = os.path.join(directory, name)
        expect_path = os.path.join(directory, name.rsplit(".", 1)[0] + ".expect.json")
        if os.path.isfile(path) and os.path.exists(expect_path):
            yield name, path, json.load(open(expect_path))


def footer_of(text):
    i = text.find(RULE)
    return text[i:] if i >= 0 else ""


def measure_one(name, path, expect, budget):
    raw = open(path, "rb").read()
    text, meta = compress.render(raw, preset=expect["preset"], max_bytes=budget,
                                 store=False)
    foot = footer_of(text)

    counts_ok = (meta["shown"] + meta["withheld"] == meta["total"]
                 and sum(meta["by_tier"].values()) == meta["total"]
                 and meta["total"] == expect["total"]
                 and meta["raw_total"] == expect["raw_total"]
                 and (expect["by_tier"] is None or meta["by_tier"] == expect["by_tier"]))
    tiers_ok = all(label in foot for label, n in meta["by_tier"].items() if n)
    silent_loss = 0 if (meta["shown"] + meta["withheld"] == meta["total"]
                        and (meta["withheld"] == 0 or "withheld" in foot)) else 1
    row_recall = 0.0 if not meta["total"] else meta["shown"] / meta["total"]

    # alert_recall is gated at EVERY budget, not just the full one. The rule is
    # not "recall is always 1.0" — at a small enough budget the alerts alone
    # cannot fit — it is "if recall < 1.0, say so exactly" (0 rule 1). So a
    # short slice passes only when it set alert_truncated AND put the exact
    # withheld count in the footer. That is satisfiable at every budget and is
    # strictly stricter than scoping the gate to 12288 and skipping it below,
    # which hid a real 0.50 recall on unknown-json at 4096.
    missing = meta["alert_count"] - meta["alert_shown"]
    # The EXACT rendered line, not `str(missing) in foot` — that was satisfied
    # by any digit anywhere (a byte count, "shown 2", "alert recall 0.2x") and
    # caught only 1 of 22 deliberately falsified footers.
    honest_about_alerts = (
        meta["alert_recall"] == 1.0
        or (meta["alert_truncated"] and missing > 0
            and f"\u26a0 {missing} alerts withheld" in foot))
    gates = {"count_accuracy": 1.0 if counts_ok else 0.0,
             "tier_presence": 1.0 if tiers_ok else 0.0,
             "silent_loss": silent_loss,
             "alert_recall": 1.0 if honest_about_alerts else 0.0}
    if budget >= FULL_BUDGET:
        # 8.1's own row: at the full budget nothing should truncate at all,
        # unless the fixture is the one built to demonstrate overflow.
        want_trunc = bool(expect.get("expect_alert_truncated"))
        gates["alert_truncated"] = 1.0 if meta["alert_truncated"] == want_trunc else 0.0

    failed = [k for k, v in gates.items()
              if (v != 0 if k == "silent_loss" else v != 1.0)]
    return meta, gates, failed, row_recall, bytelen(text)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("directory", nargs="?",
                    default=os.path.join(ROOT, "tests", "fixtures"))
    ap.add_argument("--budgets", default="1024,4096,12288")
    a = ap.parse_args(argv)
    budgets = [int(b) for b in a.budgets.split(",") if b.strip()]

    print(f"{'fixture':<22}{'budget':>7}{'rows':>12}{'alert':>12}"
          f"{'recall':>8}{'reduction':>11}  gates")
    print("-" * 86)
    failures = []
    for name, path, expect in fixtures(a.directory):
        for budget in budgets:
            meta, gates, failed, row_recall, size = measure_one(name, path, expect, budget)
            status = "ok" if not failed else "FAIL " + ",".join(failed)
            trunc = " !trunc" if meta["alert_truncated"] else ""
            print(f"{name:<22}{budget:>7}"
                  f"{meta['shown']:>6}/{meta['total']:<5}"
                  f"{meta['alert_shown']:>6}/{meta['alert_count']:<5}"
                  f"{meta['alert_recall']:>8.2f}"
                  f"{meta['reduction'] * 100:>10.0f}%  {status}{trunc}")
            if failed:
                failures.append((name, budget, failed))
    print("-" * 86)
    if failures:
        print(f"HARD GATE FAILURES: {len(failures)}")
        for name, budget, failed in failures:
            print(f"  {name} @ {budget}: {', '.join(failed)}")
        return 1
    print(f"all hard gates green over {len(budgets)} budgets")
    return 0


if __name__ == "__main__":
    sys.exit(main())
