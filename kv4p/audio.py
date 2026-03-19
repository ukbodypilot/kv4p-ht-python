"""Opus audio encoding/decoding and DSP helpers for KV4P HT.

Matches the firmware's audio pipeline:
  ESP32 RX: ADC → DC offset removal → 16x gain → squelch mute → Opus (AUDIO, NB, VBR)
  ESP32 TX: Opus decode (VOIP, NB, VBR) → I2S DAC (PDM)
"""

from __future__ import annotations

import logging
import math
import struct
from typing import Optional

log = logging.getLogger(__name__)

# Opus parameters matching KV4P HT firmware
SAMPLE_RATE = 48000
CHANNELS = 1
FRAME_MS = 40
FRAME_SIZE = SAMPLE_RATE * FRAME_MS // 1000  # 1920 samples


class OpusEncoder:
    """Wrap opuslib encoder with KV4P HT TX settings.

    Uses APPLICATION_VOIP (matching Android app TX encoder),
    narrowband bandwidth, VBR enabled.
    """

    def __init__(self) -> None:
        import opuslib
        self._enc = opuslib.Encoder(SAMPLE_RATE, CHANNELS, opuslib.APPLICATION_VOIP)
        self._enc.bandwidth = opuslib.BANDWIDTH_NARROWBAND
        self._enc.vbr = True

    def encode(self, pcm_s16: bytes) -> bytes:
        """Encode a 40ms frame of signed 16-bit LE PCM to Opus."""
        return self._enc.encode(pcm_s16, FRAME_SIZE)


class OpusDecoder:
    """Wrap opuslib decoder with KV4P HT RX settings."""

    def __init__(self) -> None:
        import opuslib
        self._dec = opuslib.Decoder(SAMPLE_RATE, CHANNELS)

    def decode(self, opus_data: bytes) -> bytes:
        """Decode an Opus frame to signed 16-bit LE PCM."""
        return self._dec.decode(opus_data, FRAME_SIZE)


class DCOffsetRemover:
    """DC offset removal using exponential decay, matching ESP32 firmware.

    The firmware uses: alpha = 1.0 - exp(-1.0 / (sample_rate * (decay_time / ln(2))))
    prev_y = alpha * x + (1 - alpha) * prev_y
    output = x - prev_y
    """

    def __init__(self, decay_time: float = 0.25, sample_rate: int = SAMPLE_RATE) -> None:
        self.alpha = 1.0 - math.exp(-1.0 / (sample_rate * (decay_time / math.log(2.0))))
        self.prev_y = 0.0

    def process(self, pcm_s16: bytes) -> bytes:
        """Remove DC offset from a PCM s16 LE buffer."""
        n = len(pcm_s16) // 2
        samples = list(struct.unpack(f"<{n}h", pcm_s16))
        out = []
        for x in samples:
            self.prev_y = self.alpha * x + (1.0 - self.alpha) * self.prev_y
            out.append(max(-32768, min(32767, int(x - self.prev_y))))
        return struct.pack(f"<{n}h", *out)

    def reset(self) -> None:
        self.prev_y = 0.0


class VolumeRamp:
    """Smooth volume ramp-up to prevent click/pop, matching Android app.

    Uses exponential smoothing: V(n) = alpha + (1 - alpha) * V(n-1)
    Mutes below threshold to avoid low-level noise during ramp.
    """

    def __init__(self, alpha: float = 0.05, threshold: float = 0.7) -> None:
        self.alpha = alpha
        self.threshold = threshold
        self._volume = 0.0
        self._active = False

    def start(self) -> None:
        """Call when audio stream starts (e.g. squelch opens)."""
        self._volume = 0.0
        self._active = True

    def stop(self) -> None:
        """Call when audio stream stops."""
        self._active = False
        self._volume = 0.0

    def process(self, pcm_s16: bytes) -> bytes:
        """Apply volume ramp to PCM s16 LE buffer."""
        if not self._active:
            return pcm_s16

        # Ramp up
        self._volume = self.alpha + (1.0 - self.alpha) * self._volume

        # Below threshold: mute to avoid noise during ramp
        if self._volume < self.threshold:
            return b'\x00' * len(pcm_s16)

        # At or above threshold: apply volume (will be ~0.7 to 1.0)
        if self._volume >= 0.99:
            return pcm_s16  # Fully ramped — pass through

        n = len(pcm_s16) // 2
        samples = struct.unpack(f"<{n}h", pcm_s16)
        vol = self._volume
        out = [max(-32768, min(32767, int(s * vol))) for s in samples]
        return struct.pack(f"<{n}h", *out)

    @property
    def is_ramped(self) -> bool:
        return self._volume >= self.threshold


class ClickRemover:
    """Remove periodic click artifacts from KV4P HT RX audio using median filter.

    The KV4P HT firmware produces ~5Hz clicks due to I2S DMA buffer
    interactions with the Opus encoder. These appear as rapid waveform
    transitions within the normal signal range — traditional click detection
    can't distinguish them from real audio transients.

    A short median filter (3-5 samples) smooths these transitions without
    significantly affecting voice quality (at 48kHz, 5 samples = 0.1ms).
    """

    def __init__(self, kernel_size: int = 3) -> None:
        """
        Args:
            kernel_size: median filter kernel size (must be odd). 3 is subtle,
                        5 is more aggressive. Higher values muffle the audio.
        """
        self.kernel_size = kernel_size | 1  # Ensure odd

    def process(self, pcm_s16: bytes) -> bytes:
        """Apply median filter to PCM s16 LE buffer."""
        try:
            from scipy.signal import medfilt
            import numpy as np
            samples = np.frombuffer(pcm_s16, dtype=np.int16).astype(np.float32)
            filtered = medfilt(samples, kernel_size=self.kernel_size)
            return filtered.astype(np.int16).tobytes()
        except ImportError:
            # scipy not available — try numpy-only median
            try:
                import numpy as np
                samples = np.frombuffer(pcm_s16, dtype=np.int16).astype(np.float32)
                n = len(samples)
                k = self.kernel_size
                pad = k // 2
                padded = np.pad(samples, pad, mode='edge')
                # Sliding window median using stride tricks
                shape = (n, k)
                strides = (padded.strides[0], padded.strides[0])
                windows = np.lib.stride_tricks.as_strided(padded, shape=shape, strides=strides)
                filtered = np.median(windows, axis=1)
                return filtered.astype(np.int16).tobytes()
            except Exception:
                return pcm_s16


def pcm_to_float(pcm_s16: bytes) -> list[float]:
    """Convert signed 16-bit LE PCM bytes to float samples (-1.0 to 1.0)."""
    n = len(pcm_s16) // 2
    samples = struct.unpack(f"<{n}h", pcm_s16)
    return [s / 32768.0 for s in samples]


def float_to_pcm(samples: list[float]) -> bytes:
    """Convert float samples (-1.0 to 1.0) to signed 16-bit LE PCM bytes."""
    clamped = [max(-1.0, min(1.0, s)) for s in samples]
    ints = [int(s * 32767) for s in clamped]
    return struct.pack(f"<{len(ints)}h", *ints)
