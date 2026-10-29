"""Tiny WAV writer used by the telephony simulator (keeps dev fixtures dependency-free)."""

from __future__ import annotations

import math
import struct


def silence_wav(seconds: float = 2.0, rate: int = 8000, tone_hz: float = 0.0) -> bytes:
    frames = int(seconds * rate)
    amp = 12000 if tone_hz else 0
    data = b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * tone_hz * i / rate))) for i in range(frames))
    header = b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack("<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16)
    return header + b"data" + struct.pack("<I", len(data)) + data


def duration_of_wav(payload: bytes) -> int:
    try:
        byte_rate = struct.unpack("<I", payload[28:32])[0]
        data_len = struct.unpack("<I", payload[40:44])[0]
        return max(1, int(data_len / byte_rate)) if byte_rate else 0
    except Exception:
        return 0
