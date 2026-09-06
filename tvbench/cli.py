"""Command line entry points."""

import argparse
import asyncio
import json
import logging
import os
import platform
import statistics
import sys
import time
from datetime import datetime, timezone

from . import __version__
from .harness import Session, register_call, scenario_bargein, scenario_greeting
from .metrics import summarise, verdicts
from .reference_agent import ReferenceAgent, serve


def _environment() -> dict:
    return {
        "tvbench": __version__,
        "python": platform.python_version(),
        "system": f"{platform.system()} {platform.release()}",
        "machine": platform.machine(),
        "cpu_count": os.cpu_count(),
        "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


async def _one_run(args, scenario: str) -> dict:
    session = Session(args.host, args.port)
    if args.register_url:
        register_call(args.register_url, session.call_id, args.persona, args.caller)
    async with session:
        if scenario == "greeting":
            await scenario_greeting(session, args.listen)
        else:
            await scenario_bargein(session, args.cut_after, args.talk)
    return summarise(session.capture, scenario)


def _aggregate(runs: list) -> dict:
    """Medians across runs, because one run of anything is an anecdote."""
    def collect(path):
        out = []
        for r in runs:
            cur = r
            for key in path:
                cur = cur.get(key) if isinstance(cur, dict) else None
                if cur is None:
                    break
            if isinstance(cur, (int, float)):
                out.append(cur)
        return out

    fields = [
        ("opening", "first_audible_ms"),
        ("pacing", "worst_burst_frames"),
        ("pacing", "realtime_ratio"),
        ("pacing", "median_gap_ms"),
        ("continuity", "worst_gap_ms"),
        ("barge_in", "cut_ms"),
        ("barge_in", "audio_wasted_ms"),
    ]
    med = {}
    for path in fields:
        vals = collect(path)
        if vals:
            med[".".join(path)] = {
                "median": round(statistics.median(vals), 2),
                "min": round(min(vals), 2),
                "max": round(max(vals), 2),
                "n": len(vals),
            }
    return med


def cmd_run(args) -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    runs = []
    for i in range(args.runs):
        if i:
            time.sleep(args.pause)
        try:
            runs.append(asyncio.run(_one_run(args, args.scenario)))
        except Exception as e:
            print(f"run {i + 1} failed: {e}", file=sys.stderr)
            return 2

    report = {
        "target": {"host": args.host, "port": args.port, "label": args.label},
        "scenario": args.scenario,
        "runs": runs,
        "median": _aggregate(runs),
        "environment": _environment(),
    }

    print(f"\n{args.label or f'{args.host}:{args.port}'}  scenario={args.scenario}  runs={len(runs)}")
    for key, val in report["median"].items():
        print(f"  {key:34} median {val['median']:>9}   range {val['min']} to {val['max']}")
    print()
    for line in verdicts(runs[-1]):
        print(f"  {line}")

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2)
        print(f"\nwritten to {args.out}")
    return 0


def cmd_reference(args) -> int:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    agent = ReferenceAgent(think=args.think, speak=args.speak, mode=args.mode,
                           gap=args.gap, react=args.react, preroll=args.preroll)
    try:
        asyncio.run(serve(args.host, args.port, agent))
    except KeyboardInterrupt:
        pass
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="tvbench",
        description="Measure what a caller actually hears from a telephony voice agent.")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="benchmark an agent over AudioSocket")
    r.add_argument("--host", default="127.0.0.1")
    r.add_argument("--port", type=int, default=9092)
    r.add_argument("--scenario", choices=["greeting", "bargein"], default="greeting")
    r.add_argument("--runs", type=int, default=5)
    r.add_argument("--pause", type=float, default=1.0,
                   help="seconds between runs, so a cache-warming first run is visible")
    r.add_argument("--listen", type=float, default=20.0,
                   help="greeting scenario: how long to stay on the call")
    r.add_argument("--cut-after", type=float, default=1.0,
                   help="bargein scenario: seconds into the agent's speech before talking over it")
    r.add_argument("--talk", type=float, default=1.5,
                   help="bargein scenario: how long the caller talks")
    r.add_argument("--register-url", default=None,
                   help="optional pre-registration endpoint, for agents with a call allowlist")
    r.add_argument("--persona", default="demo")
    r.add_argument("--caller", default="1000")
    r.add_argument("--label", default=None, help="name for this target in the report")
    r.add_argument("--out", default=None, help="write the full report as JSON here")
    r.set_defaults(func=cmd_run)

    a = sub.add_parser("reference", help="run the reference agent to check the harness")
    a.add_argument("--host", default="127.0.0.1")
    a.add_argument("--port", type=int, default=9092)
    a.add_argument("--mode", choices=["paced", "burst", "gappy", "deaf"], default="paced")
    a.add_argument("--think", type=float, default=0.4)
    a.add_argument("--speak", type=float, default=6.0)
    a.add_argument("--gap", type=float, default=0.5)
    a.add_argument("--react", type=float, default=0.06)
    a.add_argument("--preroll", type=int, default=0)
    a.set_defaults(func=cmd_reference)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
