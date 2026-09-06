"""The harness pretends to be Asterisk.

Nothing here knows anything about the agent under test beyond the AudioSocket
protocol. It dials in, paces caller audio at the same 20 ms cadence a real
channel would, and timestamps every frame the agent sends back. Those arrival
times are the measurement: they are what a caller would have heard and when.

Two deliberate choices are worth knowing about.

The sender clamps its own deadline every frame rather than trying to catch up
after a stall, because a harness that bursts is measuring itself rather than the
agent. And the receiver timestamps on arrival rather than after any buffering,
because a timestamp taken after a queue is a timestamp of the queue.
"""

import asyncio
import json
import time
import urllib.request
import uuid as _uuid
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .audiosocket import (
    FRAME_BYTES, FRAME_SEC, Frame, FrameType, audio_frame, hangup_frame,
    uuid_frame,
)
from .tone import frame_energy, silence, speech_like, to_frames


@dataclass
class Capture:
    """Everything the harness saw, with timestamps relative to call start."""

    call_started: float = 0.0
    frames: List[Tuple[float, bytes]] = field(default_factory=list)
    barge_in_at: Optional[float] = None
    hangup_at: Optional[float] = None
    error: Optional[str] = None

    def rel(self, t: float) -> float:
        return t - self.call_started


def register_call(register_url: str, call_id: str, persona: str, caller: str) -> None:
    """Pre-register the call id, for agents that refuse unknown ids.

    Optional and agent specific. asterisk-ai-voice-agent requires it; an agent
    that accepts any inbound call does not need this and the flag is left unset.
    """
    body = json.dumps({"uuid": call_id, "persona": persona, "caller": caller}).encode()
    req = urllib.request.Request(
        register_url, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp.read()


class Session:
    """One benchmark call against an agent's AudioSocket port."""

    def __init__(self, host: str, port: int, call_id: Optional[str] = None):
        self.host = host
        self.port = port
        self.call_id = call_id or str(_uuid.uuid4())
        self.capture = Capture()
        self._reader = None
        self._writer = None
        self._stop = asyncio.Event()

    async def __aenter__(self):
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port)
        self.capture.call_started = time.monotonic()
        self._writer.write(uuid_frame(self.call_id).encode())
        await self._writer.drain()
        self._rx = asyncio.create_task(self._receive())
        return self

    async def __aexit__(self, *exc):
        self._stop.set()
        try:
            self._writer.write(hangup_frame().encode())
            await self._writer.drain()
        except Exception:
            pass
        self._rx.cancel()
        try:
            await self._rx
        except (asyncio.CancelledError, Exception):
            pass
        try:
            self._writer.close()
        except Exception:
            pass

    async def _receive(self) -> None:
        try:
            while not self._stop.is_set():
                frame = await Frame.read(self._reader)
                if frame is None:
                    return
                now = time.monotonic()
                if frame.type == FrameType.AUDIO:
                    self.capture.frames.append((now, frame.payload))
                elif frame.type == FrameType.HANGUP:
                    self.capture.hangup_at = now
                    return
                elif frame.type == FrameType.ERROR:
                    self.capture.error = frame.payload.hex()
        except asyncio.IncompleteReadError:
            return
        except ConnectionResetError:
            return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            self.capture.error = str(e)

    async def send(self, pcm) -> None:
        """Send PCM to the agent at real time, one 20 ms frame per interval."""
        deadline = time.monotonic()
        for chunk in to_frames(pcm):
            if self._stop.is_set():
                return
            try:
                self._writer.write(audio_frame(chunk).encode())
                await self._writer.drain()
            except (ConnectionResetError, BrokenPipeError):
                # The agent hung up while we were still talking. That is a
                # result, not a crash, so stop sending and let the metrics
                # describe what arrived before it went away.
                return
            deadline += FRAME_SEC
            now = time.monotonic()
            if deadline < now:
                deadline = now
            await asyncio.sleep(deadline - now)

    async def wait_for_agent_speech(self, timeout: float, threshold: float = 0.01) -> bool:
        """Block until the agent sends a frame with real audio in it."""
        end = time.monotonic() + timeout
        seen = 0
        while time.monotonic() < end:
            for _, payload in self.capture.frames[seen:]:
                if frame_energy(payload) >= threshold:
                    return True
            seen = len(self.capture.frames)
            await asyncio.sleep(0.01)
        return False

    async def quiet_for(self, seconds: float, timeout: float = 20.0) -> None:
        """Wait until the agent has sent nothing for `seconds`."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            last = self.capture.frames[-1][0] if self.capture.frames else self.capture.call_started
            if time.monotonic() - last >= seconds:
                return
            await asyncio.sleep(0.02)


async def scenario_greeting(session: Session, listen_seconds: float) -> None:
    """Answer and stay silent. Measures how the agent opens a call.

    Real callers are not silent, but they are silent at exactly this moment, and
    the greeting is the one part of a pipeline that runs without a transcript or
    a model round trip. That makes it the cleanest look at pacing there is.
    """
    sender = asyncio.create_task(session.send(silence(listen_seconds)))
    await session.wait_for_agent_speech(timeout=listen_seconds)
    await session.quiet_for(1.0, timeout=listen_seconds)
    sender.cancel()
    try:
        await sender
    except (asyncio.CancelledError, Exception):
        pass


async def scenario_bargein(session: Session, cut_after: float,
                           talk_seconds: float, settle: float = 3.0) -> None:
    """Let the agent start talking, then talk over it.

    `cut_after` is measured from the agent's first audible frame, not from the
    call starting, so the interruption lands mid-sentence regardless of how long
    the agent took to open its mouth.
    """
    quiet = asyncio.create_task(session.send(silence(30.0)))
    got = await session.wait_for_agent_speech(timeout=25.0)
    if not got:
        quiet.cancel()
        raise RuntimeError("agent never produced audio; nothing to interrupt")
    await asyncio.sleep(cut_after)
    quiet.cancel()
    try:
        await quiet
    except (asyncio.CancelledError, Exception):
        pass
    session.capture.barge_in_at = time.monotonic()
    await session.send(speech_like(talk_seconds))
    await session.quiet_for(settle, timeout=settle + 10.0)
