"""A deliberately simple agent, so the harness can be checked against known behaviour.

This is not a voice agent. It has no speech recognition, no model and no
synthesis. What it has is the timing behaviour of a voice agent, which is the
only thing the benchmark measures, and switches to turn each behaviour on and
off.

Its real job is falsification. A benchmark that only ever sees well-behaved
agents proves nothing, so this can be told to burst, to leave holes, and to
ignore an interrupting caller. If the numbers do not move when the behaviour
changes, the benchmark is broken and the results are worthless.
"""

import asyncio
import logging
import time
from typing import Optional

from .audiosocket import (
    FRAME_BYTES, FRAME_SEC, Frame, FrameType, audio_frame, SILENCE_FRAME,
)
from .tone import frame_energy, speech_like, to_frames

log = logging.getLogger("tvbench.reference")


class ReferenceAgent:
    """Behaviours:

    paced       release one frame every 20 ms on a clamped deadline
    burst       write the whole utterance at once, the classic AudioSocket bug
    gappy       paced, but stop for `gap` seconds partway, like synthesising the
                next sentence only after the current one has finished playing
    deaf        paced, but never stop for an interrupting caller
    """

    def __init__(self, think: float = 0.4, speak: float = 6.0, mode: str = "paced",
                 gap: float = 0.5, react: float = 0.06, preroll: int = 0):
        self.think = think
        self.speak = speak
        self.mode = mode
        self.gap = gap
        self.react = react
        self.preroll = preroll

    async def handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        log.info("call from %s in %s mode", peer, self.mode)
        interrupted = asyncio.Event()
        rx = asyncio.create_task(self._listen(reader, interrupted))
        try:
            await asyncio.sleep(self.think)
            for _ in range(self.preroll):
                writer.write(audio_frame(SILENCE_FRAME).encode())
            await self._speak(writer, interrupted)
            # Stay on the call the way a real agent does: after speaking it goes
            # back to listening. Hanging up here would make the harness's own
            # send fail and turn a barge-in measurement into a socket error.
            try:
                await asyncio.wait_for(asyncio.shield(rx), timeout=30.0)
            except (asyncio.TimeoutError, Exception):
                pass
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            rx.cancel()
            try:
                await rx
            except (asyncio.CancelledError, Exception):
                pass
            try:
                writer.close()
            except Exception:
                pass

    async def _listen(self, reader: asyncio.StreamReader,
                      interrupted: asyncio.Event) -> None:
        """Set the interrupt flag once the caller has been talking for a moment.

        Six consecutive voiced frames is 120 ms, long enough to ignore a cough
        and short enough that a real interruption is caught quickly.
        """
        run = 0
        try:
            while True:
                frame = await Frame.read(reader)
                if frame is None or frame.type == FrameType.HANGUP:
                    interrupted.set()
                    return
                if frame.type != FrameType.AUDIO:
                    continue
                if frame_energy(frame.payload) >= 0.01:
                    run += 1
                    if run >= 6:
                        interrupted.set()
                else:
                    run = 0
        except (asyncio.IncompleteReadError, ConnectionResetError):
            interrupted.set()
        except asyncio.CancelledError:
            raise

    async def _speak(self, writer: asyncio.StreamWriter,
                     interrupted: asyncio.Event) -> None:
        frames = to_frames(speech_like(self.speak))
        stops = self.mode != "deaf"

        if self.mode == "burst":
            # Everything at once. On a real channel the far end keeps a few
            # frames and discards the rest.
            for chunk in frames:
                writer.write(audio_frame(chunk).encode())
            await writer.drain()
            return

        gap_at = len(frames) // 2 if self.mode == "gappy" else -1
        deadline = time.monotonic()
        for i, chunk in enumerate(frames):
            if stops and interrupted.is_set():
                await asyncio.sleep(self.react)
                return
            if i == gap_at:
                await asyncio.sleep(self.gap)
                deadline = time.monotonic()
            writer.write(audio_frame(chunk).encode())
            await writer.drain()
            deadline += FRAME_SEC
            now = time.monotonic()
            if deadline < now:
                deadline = now
            await asyncio.sleep(deadline - now)


async def serve(host: str, port: int, agent: ReferenceAgent) -> None:
    server = await asyncio.start_server(agent.handle, host, port)
    log.info("reference agent listening on %s:%s (%s)", host, port, agent.mode)
    async with server:
        await server.serve_forever()
