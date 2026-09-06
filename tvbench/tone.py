"""Caller audio generation.

The benchmark has to sound like a person to whatever voice activity detector the
agent under test is running, without shipping a speech corpus or depending on a
model. What it generates instead is a band-limited noise burst amplitude
modulated at roughly syllable rate, which webrtcvad and the common energy based
detectors accept as speech, and which is deterministic given a seed so two runs
on two machines produce the same input.

This is a deliberate limitation and it is stated in the README: these signals
exercise endpointing and barge-in timing, not transcription accuracy. A
benchmark that claimed to measure word error rate from synthetic noise would be
lying.
"""

import numpy as np

from .audiosocket import FRAME_BYTES, SAMPLE_RATE

_SYLLABLE_HZ = 4.5


def speech_like(seconds: float, seed: int = 7, level: float = 0.28) -> np.ndarray:
    """Voiced-sounding noise, `seconds` long, as int16 at 8 kHz."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    if n <= 0:
        return np.zeros(0, dtype=np.int16)
    t = np.arange(n) / SAMPLE_RATE

    # Broadband excitation shaped toward the telephone band.
    noise = rng.standard_normal(n)
    # Simple one-pole high pass then low pass, cheap and dependency free.
    hp = np.empty(n)
    prev_x = prev_y = 0.0
    a = 0.92
    for i in range(n):
        y = a * (prev_y + noise[i] - prev_x)
        hp[i] = y
        prev_x, prev_y = noise[i], y
    lp = np.convolve(hp, np.ones(6) / 6.0, mode="same")

    # A voiced carrier so the result has periodicity, plus syllable-rate
    # amplitude modulation so it starts and stops the way talking does.
    carrier = 0.6 + 0.4 * np.sin(2 * np.pi * 130.0 * t)
    envelope = 0.55 + 0.45 * np.sin(2 * np.pi * _SYLLABLE_HZ * t) ** 2
    sig = lp * carrier * envelope

    peak = np.max(np.abs(sig)) or 1.0
    sig = (sig / peak) * level * 32767.0
    return sig.astype(np.int16)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.int16)


def to_frames(pcm: np.ndarray) -> list:
    """Split int16 PCM into 320-byte frames, padding the tail."""
    raw = pcm.astype(np.int16).tobytes()
    out = []
    for off in range(0, len(raw), FRAME_BYTES):
        chunk = raw[off:off + FRAME_BYTES]
        if len(chunk) < FRAME_BYTES:
            chunk = chunk + b"\x00" * (FRAME_BYTES - len(chunk))
        out.append(chunk)
    return out


def frame_energy(frame: bytes) -> float:
    """RMS of one frame, 0.0 to 1.0. Used to tell agent speech from silence."""
    if not frame:
        return 0.0
    pcm = np.frombuffer(frame, dtype=np.int16).astype(np.float32)
    if pcm.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(pcm * pcm)) / 32768.0)
