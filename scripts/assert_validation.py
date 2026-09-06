#!/usr/bin/env python
"""Fail the build if the benchmark has stopped discriminating.

`validate.py` produces the numbers. This decides whether they still mean
anything. The thresholds are deliberately loose, because the point is not to pin
timing on a shared CI runner, it is to catch the day a refactor quietly makes
every agent look identical.

    python scripts/assert_validation.py
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "results", "reference-validation.json")


def median(case, key):
    return case.get("median", {}).get(key, {}).get("median")


def main() -> int:
    with open(PATH, encoding="utf-8") as f:
        data = json.load(f)
    cases = {(c["mode"], c["scenario"]): c for c in data["cases"]}

    checks = []

    paced = cases.get(("paced", "greeting"))
    burst = cases.get(("burst", "greeting"))
    gappy = cases.get(("gappy", "greeting"))
    stops = cases.get(("paced", "bargein"))
    deaf = cases.get(("deaf", "bargein"))
    for name, case in [("paced/greeting", paced), ("burst/greeting", burst),
                       ("gappy/greeting", gappy), ("paced/bargein", stops),
                       ("deaf/bargein", deaf)]:
        if case is None:
            print(f"FAIL missing case {name}")
            return 1

    checks.append((
        "bursting is detected",
        median(burst, "pacing.worst_burst_frames") > 20,
        f"burst worst_burst_frames={median(burst, 'pacing.worst_burst_frames')}"))
    checks.append((
        "correct pacing is not flagged as bursting",
        median(paced, "pacing.worst_burst_frames") <= 4,
        f"paced worst_burst_frames={median(paced, 'pacing.worst_burst_frames')}"))
    checks.append((
        "bursting shows up in the realtime ratio",
        median(burst, "pacing.realtime_ratio") > 5,
        f"burst realtime_ratio={median(burst, 'pacing.realtime_ratio')}"))
    checks.append((
        "a mid-turn stall is detected",
        median(gappy, "continuity.worst_gap_ms") > 300,
        f"gappy worst_gap_ms={median(gappy, 'continuity.worst_gap_ms')}"))
    checks.append((
        "a continuous turn is not flagged as gappy",
        median(paced, "continuity.worst_gap_ms") < 200,
        f"paced worst_gap_ms={median(paced, 'continuity.worst_gap_ms')}"))
    checks.append((
        "an agent that stops on barge-in measures short",
        median(stops, "barge_in.cut_ms") < 700,
        f"paced cut_ms={median(stops, 'barge_in.cut_ms')}"))
    checks.append((
        "an agent that ignores barge-in measures long",
        median(deaf, "barge_in.cut_ms") > 1500,
        f"deaf cut_ms={median(deaf, 'barge_in.cut_ms')}"))
    checks.append((
        "the two barge-in behaviours are clearly separated",
        median(deaf, "barge_in.cut_ms") > 3 * median(stops, "barge_in.cut_ms"),
        f"deaf={median(deaf, 'barge_in.cut_ms')} vs paced={median(stops, 'barge_in.cut_ms')}"))

    failed = 0
    for label, ok, detail in checks:
        print(f"{'ok  ' if ok else 'FAIL'}  {label}  ({detail})")
        if not ok:
            failed += 1
    if failed:
        print(f"\n{failed} check(s) failed: the benchmark is no longer discriminating.")
    else:
        print(f"\nall {len(checks)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
