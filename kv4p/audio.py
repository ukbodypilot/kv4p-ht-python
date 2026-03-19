"""Opus audio encoding/decoding helpers for KV4P HT."""

from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger(__name__)

# Opus parameters matching KV4P HT firmware
SAMPLE_RATE = 48000
CHANNELS = 1
FRAME_MS = 40
FRAME_SIZE = SAMPLE_RATE * FRAME_MS // 1000  # 1920 samples


class OpusEncoder:
    """Wrap opuslib encoder with KV4P HT settings."""

    def __init__(self) -> None:
        import opuslib
        self._enc = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP)
        self._enc.bandwidth = opuslib.BANDWIDTH_NARROWBAND
        self._enc.vbr = True

    def encode(self, pcm_s16: bytes) -> bytes:
        """Encode a 40ms frame of signed 16-bit LE PCM to Opus."""
        return self._enc.encode(pcm_s16, FRAME_SIZE)


class OpusDecoder:
    """Wrap opuslib decoder with KV4P HT settings."""

    def __init__(self) -> None:
        import opuslib
        self._dec = opuslib.Decoder(SAMPLE_RATE, CHANNELS)

    def decode(self, opus_data: bytes) -> bytes:
        """Decode an Opus frame to signed 16-bit LE PCM."""
        return self._dec.decode(opus_data, FRAME_SIZE)


def pcm_to_float(pcm_s16: bytes) -> list[float]:
    """Convert signed 16-bit LE PCM bytes to float samples (-1.0 to 1.0)."""
    import struct
    n = len(pcm_s16) // 2
    samples = struct.unpack(f"<{n}h", pcm_s16)
    return [s / 32768.0 for s in samples]


def float_to_pcm(samples: list[float]) -> bytes:
    """Convert float samples (-1.0 to 1.0) to signed 16-bit LE PCM bytes."""
    import struct
    clamped = [max(-1.0, min(1.0, s)) for s in samples]
    ints = [int(s * 32767) for s in clamped]
    return struct.pack(f"<{len(ints)}h", *ints)
