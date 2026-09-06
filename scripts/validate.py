#!/usr/bin/env python
"""Run the benchmark against every reference behaviour and write the results.

This is the falsification pass. Each row switches on one known defect, and the
metric that is supposed to catch it has to move. If `burst` does not raise
worst_burst_frames, or `deaf` does not raise cut_ms, the tool is not measuring
what it claims to and the numbers in results/ mean nothing.

    python scripts/validate.py
"""
import asyncio
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from tvbench.cli import _aggregate, _environment, _one_run  # noqa: E402
from tvbench.metrics import verdicts  # noqa: E402

PORT = 19092
RUNS = 5

MATRIX = [
    ("paced", "greeting", "Correct: one frame every 20 ms"),
    ("burst", "greeting", "Writes the whole utterance at once"),
    ("gappy", "greeting", "Stops mid-turn, like sentence-at-a-time synthesis"),
    ("paced", "bargein", "Stops when the caller talks over it"),
    ("deaf", "bargein", "Ignores the caller and keeps talking"),
]


class Args:
    host = "127.0.0.1"
    port = PORT
    register_url = None
    persona = "demo"
    caller = "1000"
    listen = 12.0
    cut_after = 1.0
    talk = 1.5


def wait_for_port(port, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.15)
    return False


def main():
    out = {"environment": _environment(), "runs": RUNS, "cases": []}
    for mode, scenario, why in MATRIX:
        proc = subprocess.Popen(
            [sys.executable, "-m", "tvbench.cli", "reference", "--port", str(PORT),
             "--mode", mode, "--think", "0.4", "--speak", "6"],
            cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            if not wait_for_port(PORT):
                raise RuntimeError(f"reference agent ({mode}) never listened")
            runs = []
            for i in range(RUNS):
                if i:
                    time.sleep(0.4)
                runs.append(asyncio.run(_one_run(Args, scenario)))
            case = {
                "mode": mode, "scenario": scenario, "behaviour": why,
                "median": _aggregate(runs), "verdicts": verdicts(runs[-1]),
            }
            out["cases"].append(case)
            print(f"\n=== {mode} / {scenario}  ({why})")
            for k, v in case["median"].items():
                print(f"    {k:34} {v['median']}")
            for line in case["verdicts"]:
                print(f"    - {line}")
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            time.sleep(0.5)

    path = os.path.join(ROOT, "results", "reference-validation.json")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    print(f"\nwritten {path}")


if __name__ == "__main__":
    main()
