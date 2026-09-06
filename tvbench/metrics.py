"""Turning arrival times into numbers that mean something to a caller.

Every metric here is computed from when audio arrived, never from how much
arrived. That distinction is the whole point of the tool. An agent that delivers
every byte of a sentence in eight milliseconds has produced a perfect byte count
and an unintelligible call, because the far end's jitter buffer keeps a handful
of frames and discards the rest.
"""

import statistics
from typing import Dict, List, Optional

from .audiosocket import FRAME_BYTES, FRAME_SEC
from .harness import Capture
from .tone import frame_energy

SPEECH_THRESHOLD = 0.01


def _audible(capture: Capture, threshold: float = SPEECH_THRESHOLD):
    return [(t, p) for t, p in capture.frames if frame_energy(p) >= threshold]


def pacing(capture: Capture) -> Dict:
    """How evenly the agent released audio.

    `worst_burst_frames` is the headline: the most 20 ms frames that landed
    inside any single 20 ms window. One is correct. Anything much above one is
    the agent handing Asterisk more audio than a channel can carry in real time,
    which is the failure that empties a jitter buffer.
    """
    frames = capture.frames
    if len(frames) < 3:
        return {"frames": len(frames), "note": "too few frames to judge pacing"}

    deltas = [(frames[i + 1][0] - frames[i][0]) * 1000.0 for i in range(len(frames) - 1)]
    ordered = sorted(deltas)

    worst = 1
    j = 0
    for i in range(len(frames)):
        while frames[i][0] - frames[j][0] > FRAME_SEC:
            j += 1
        worst = max(worst, i - j + 1)

    span = frames[-1][0] - frames[0][0]
    audio_seconds = len(frames) * FRAME_SEC
    return {
        "frames": len(frames),
        "median_gap_ms": round(statistics.median(deltas), 2),
        "p95_gap_ms": round(ordered[int(len(ordered) * 0.95)], 2),
        "max_gap_ms": round(ordered[-1], 2),
        "worst_burst_frames": worst,
        "wall_seconds": round(span, 3),
        "audio_seconds": round(audio_seconds, 3),
        "realtime_ratio": round(audio_seconds / span, 3) if span > 0 else None,
    }


def opening(capture: Capture) -> Dict:
    """How long the caller waited, and whether the wait was silence or nothing."""
    if not capture.frames:
        return {"first_frame_ms": None, "first_audible_ms": None,
                "note": "agent sent no audio at all"}
    audible = _audible(capture)
    return {
        "first_frame_ms": round(capture.rel(capture.frames[0][0]) * 1000.0, 1),
        "first_audible_ms": round(capture.rel(audible[0][0]) * 1000.0, 1) if audible else None,
        "silent_preroll_frames": len(capture.frames) - len(audible) if audible else len(capture.frames),
    }


def continuity(capture: Capture) -> Dict:
    """The longest hole inside the agent's speech.

    Measured between the first and last audible frame, so trailing silence does
    not count. A hole here is the caller hearing nothing while the agent thinks,
    which is the gap that sentence-at-a-time synthesis produces.
    """
    audible = _audible(capture)
    if len(audible) < 2:
        return {"note": "not enough audible frames to measure continuity"}
    start, end = audible[0][0], audible[-1][0]
    inside = [(t, p) for t, p in capture.frames if start <= t <= end]

    worst = 0.0
    worst_at = None
    for i in range(len(inside) - 1):
        gap = inside[i + 1][0] - inside[i][0]
        if gap > worst:
            worst, worst_at = gap, capture.rel(inside[i][0])

    quiet_frames = sum(1 for _, p in inside if frame_energy(p) < SPEECH_THRESHOLD)
    return {
        "speech_span_s": round(end - start, 3),
        "worst_gap_ms": round(worst * 1000.0, 1),
        "worst_gap_at_s": round(worst_at, 3) if worst_at is not None else None,
        "interior_silent_frames": quiet_frames,
        "interior_silent_ms": round(quiet_frames * FRAME_SEC * 1000.0, 1),
    }


def barge_in(capture: Capture) -> Dict:
    """How long the agent kept talking after the caller started.

    Timed from the first frame of caller speech the harness put on the wire to
    the last frame of agent audio that came back. It therefore includes the
    agent's voice detection, whatever it does to stop playback, and anything
    already queued that it could not take back.
    """
    if capture.barge_in_at is None:
        return {"note": "no barge-in in this scenario"}
    after = [t for t, p in capture.frames
             if t >= capture.barge_in_at and frame_energy(p) >= SPEECH_THRESHOLD]
    if not after:
        return {"cut_ms": 0.0, "frames_after_barge_in": 0,
                "note": "agent was already silent when the caller started"}
    return {
        "cut_ms": round((after[-1] - capture.barge_in_at) * 1000.0, 1),
        "frames_after_barge_in": len(after),
        "audio_wasted_ms": round(len(after) * FRAME_SEC * 1000.0, 1),
    }


def summarise(capture: Capture, scenario: str) -> Dict:
    out = {
        "scenario": scenario,
        "opening": opening(capture),
        "pacing": pacing(capture),
        "continuity": continuity(capture),
    }
    if scenario == "bargein":
        out["barge_in"] = barge_in(capture)
    if capture.error:
        out["error"] = capture.error
    return out


def verdicts(summary: Dict) -> List[str]:
    """Plain sentences a reader can act on. Deliberately few, and only where the
    number is unambiguous."""
    notes = []
    p = summary.get("pacing", {})
    burst = p.get("worst_burst_frames")
    if isinstance(burst, int):
        if burst <= 2:
            notes.append(f"Paced: at most {burst} frame(s) landed in any 20 ms window.")
        elif burst <= 10:
            notes.append(f"Mildly bursty: {burst} frames in one 20 ms window.")
        else:
            notes.append(
                f"Bursting: {burst} frames arrived inside one 20 ms window. On a real "
                "channel most of that audio is discarded by the far end.")
    ratio = p.get("realtime_ratio")
    if isinstance(ratio, float) and ratio > 1.5:
        notes.append(
            f"Delivered {ratio}x faster than real time, which is the same problem "
            "seen from the other side.")
    c = summary.get("continuity", {})
    gap = c.get("worst_gap_ms")
    if isinstance(gap, float):
        if gap < 150:
            notes.append(f"Continuous: worst hole inside the speech was {gap} ms.")
        else:
            notes.append(f"Audible hole of {gap} ms inside the agent's own turn.")
    b = summary.get("barge_in", {})
    cut = b.get("cut_ms")
    if isinstance(cut, float):
        if cut <= 250:
            notes.append(f"Barge-in cut the agent off in {cut} ms.")
        else:
            notes.append(
                f"Barge-in took {cut} ms to take effect, so the caller talked over "
                "the agent for that long.")
    return notes
