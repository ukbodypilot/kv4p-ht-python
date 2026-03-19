"""TX audio processing: boost, gate, pre-emphasis for cleaner on-air signal."""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass

SAMPLE_RATE = 48000
FRAME_SIZE = 1920  # 40ms at 48kHz


@dataclass
class TxAudioProcessor:
    """Process PCM audio before Opus encoding for cleaner TX.

    - gain: linear multiplier (1.0 = unity, 2.0 = +6dB)
    - gate_threshold: RMS below this = silence (0.0 disables gate)
    - pre_emphasis_alpha: high-freq boost coefficient (0.0 disables)
    - hard_limit: clamp samples to this fraction of full scale
    """
    gain: float = 1.0
    gate_threshold: float = 0.005
    pre_emphasis_alpha: float = 0.0
    hard_limit: float = 0.95

    def __post_init__(self):
        self._prev_sample = 0.0

    def process(self, pcm_s16: bytes) -> tuple[bytes, bool]:
        """Process a frame of signed-16 LE PCM.

        Returns (processed_pcm_s16, is_voice).
        is_voice is False if the gate determined this frame is silence.
        """
        n = len(pcm_s16) // 2
        samples = list(struct.unpack(f"<{n}h", pcm_s16))

        # Convert to float -1..1
        floats = [s / 32768.0 for s in samples]

        # Noise gate: check RMS before any processing
        rms = math.sqrt(sum(s * s for s in floats) / len(floats)) if floats else 0.0
        if self.gate_threshold > 0 and rms < self.gate_threshold:
            self._prev_sample = 0.0
            return b'\x00' * len(pcm_s16), False

        # Pre-emphasis: y[n] = x[n] - alpha * x[n-1]
        if self.pre_emphasis_alpha > 0:
            emphasized = []
            prev = self._prev_sample
            for s in floats:
                emphasized.append(s - self.pre_emphasis_alpha * prev)
                prev = s
            self._prev_sample = floats[-1] if floats else 0.0
            floats = emphasized
        else:
            self._prev_sample = floats[-1] if floats else 0.0

        # Gain
        if self.gain != 1.0:
            floats = [s * self.gain for s in floats]

        # Hard limiter (prevent clipping)
        limit = self.hard_limit
        floats = [max(-limit, min(limit, s)) for s in floats]

        # Back to s16
        out = struct.pack(f"<{n}h", *[int(s * 32767) for s in floats])
        return out, True

    def reset(self):
        """Reset pre-emphasis state (call between transmissions)."""
        self._prev_sample = 0.0


def generate_tone(freq: float = 1000.0, duration_ms: int = 3000,
                  amplitude: float = 0.9) -> list[bytes]:
    """Generate PCM s16 LE frames of a sine tone.

    Returns a list of 40ms frames ready for Opus encoding.
    """
    total_samples = SAMPLE_RATE * duration_ms // 1000
    num_frames = total_samples // FRAME_SIZE
    frames = []
    for f_idx in range(num_frames):
        offset = f_idx * FRAME_SIZE
        samples = [
            int(math.sin(2 * math.pi * freq * (offset + i) / SAMPLE_RATE)
                * amplitude * 32767)
            for i in range(FRAME_SIZE)
        ]
        frames.append(struct.pack(f"<{FRAME_SIZE}h", *samples))
    return frames


def generate_silence(duration_ms: int = 3000) -> list[bytes]:
    """Generate silent PCM s16 LE frames (all zeros).

    Returns a list of 40ms frames.
    """
    num_frames = (SAMPLE_RATE * duration_ms // 1000) // FRAME_SIZE
    silence = b'\x00' * (FRAME_SIZE * 2)
    return [silence] * num_frames
