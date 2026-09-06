"""AudioSocket frame codec.

Wire format, big-endian over TCP:

  byte 0     type
  bytes 1-2  payload length
  bytes 3+   payload

  0x00 HANGUP  no payload
  0x01 UUID    16 bytes, the call id
  0x10 AUDIO   slin, 16-bit signed linear, 8 kHz mono, 320 bytes per 20 ms
  0xff ERROR   1 byte error code

Reference: app_audiosocket.c in the Asterisk source tree. Note that `slin` is
8 kHz in Asterisk naming; `slin16` is the 16 kHz variant and would be 640 bytes
per 20 ms frame.
"""

import asyncio
import struct
import uuid as _uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import Optional

FRAME_BYTES = 320
FRAME_SEC = 0.02
SAMPLE_RATE = 8000
SILENCE_FRAME = b"\x00" * FRAME_BYTES


class FrameType(IntEnum):
    HANGUP = 0x00
    UUID = 0x01
    AUDIO = 0x10
    ERROR = 0xFF


@dataclass
class Frame:
    type: FrameType
    payload: bytes

    @classmethod
    async def read(cls, reader: asyncio.StreamReader) -> "Optional[Frame]":
        header = await reader.readexactly(3)
        ftype, length = struct.unpack(">BH", header)
        payload = await reader.readexactly(length) if length else b""
        try:
            ftype = FrameType(ftype)
        except ValueError:
            ftype = FrameType.ERROR
        return cls(type=ftype, payload=payload)

    def encode(self) -> bytes:
        return struct.pack(">BH", int(self.type), len(self.payload)) + self.payload


def uuid_frame(call_id: str) -> Frame:
    return Frame(type=FrameType.UUID, payload=_uuid.UUID(call_id).bytes)


def audio_frame(samples: bytes) -> Frame:
    return Frame(type=FrameType.AUDIO, payload=samples)


def hangup_frame() -> Frame:
    return Frame(type=FrameType.HANGUP, payload=b"")
