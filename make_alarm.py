"""
make_alarm.py
=============
Generates ``static/alarm.wav`` - the loud two-tone siren used by the Spar
Security Command Dashboard.

The sound is *synthesised locally with the Python standard library only*
(``wave`` + ``math``), so the project keeps its promise of being 100 %
offline: no CDN, no cloud storage, no paid sound library, nothing to
download during the defence.

Sound design (classic "ring-ring" security siren, 1.60 s loop):

    0.00 s - 0.35 s   burst 1 : warbling tone alternating 880 Hz / 1180 Hz
    0.35 s - 0.45 s   gap
    0.45 s - 0.80 s   burst 2 : same warble
    0.80 s - 1.60 s   silence (so the repetition sounds like a real alarm)

Run directly with::

    python make_alarm.py
"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from utils import ALARM_WAV, ALARM_WAV_COPY

SAMPLE_RATE = 16_000          # Hz - plenty for an alarm, keeps the file small
DURATION_S = 1.60             # one full ring cycle
LOW_HZ = 880.0                # warble low tone
HIGH_HZ = 1180.0              # warble high tone
WARBLE_PERIOD_S = 0.045       # time between tone changes
BURST_S = 0.35                # length of each of the two siren bursts
AMPLITUDE = 0.88              # 0.0 - 1.0  (loud!)
FADE_S = 0.008                # click-free attack / release


def _burst_sample(t_in_burst: float, phase_ref: float) -> tuple[float, float]:
    """Return (sample, new_phase) for a warbling square-ish tone."""
    # Alternate between LOW_HZ and HIGH_HZ every WARBLE_PERIOD_S seconds.
    step = int(t_in_burst / WARBLE_PERIOD_S)
    freq = HIGH_HZ if (step % 2 == 0) else LOW_HZ

    # A touch of the third harmonic makes the tone cut through a noisy mall.
    value = math.sin(phase_ref) * 0.86 + math.sin(3.0 * phase_ref) * 0.14

    # Click-free envelope at the start and end of each burst.
    if t_in_burst < FADE_S:
        value *= t_in_burst / FADE_S
    tail = BURST_S - t_in_burst
    if tail < FADE_S:
        value *= max(0.0, tail / FADE_S)

    phase_ref += 2.0 * math.pi * freq / SAMPLE_RATE
    if phase_ref > 2.0 * math.pi:
        phase_ref -= 2.0 * math.pi
    return value, phase_ref


def build_alarm_bytes() -> bytes:
    """Synthesise the siren and return raw 16-bit PCM frames."""
    total_samples = int(SAMPLE_RATE * DURATION_S)
    frames = bytearray()
    phase = 0.0

    for index in range(total_samples):
        t = index / SAMPLE_RATE

        in_burst_1 = 0.00 <= t < 0.35
        in_burst_2 = 0.45 <= t < 0.80

        value = 0.0
        if in_burst_1:
            value, phase = _burst_sample(t - 0.00, phase)
        elif in_burst_2:
            value, phase = _burst_sample(t - 0.45, phase)
        else:
            phase = 0.0  # silence keeps the loop phase-aligned and click-free

        sample = int(max(-1.0, min(1.0, value * AMPLITUDE)) * 32_767)
        frames += struct.pack("<h", sample)

    return bytes(frames)


def generate_alarm(force: bool = False) -> tuple[Path, int]:
    """Write the siren to ``static/`` (and a copy in ``assets/``).

    Returns the primary path and the file size in bytes.
    """
    if ALARM_WAV.exists() and ALARM_WAV.stat().st_size > 1024 and not force:
        return ALARM_WAV, ALARM_WAV.stat().st_size

    pcm = build_alarm_bytes()

    for target in (ALARM_WAV, ALARM_WAV_COPY):
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as handle:
            handle.setnchannels(1)          # mono
            handle.setsampwidth(2)          # 16-bit
            handle.setframerate(SAMPLE_RATE)
            handle.writeframes(pcm)

    return ALARM_WAV, ALARM_WAV.stat().st_size


if __name__ == "__main__":  # pragma: no cover
    path, size = generate_alarm(force=True)
    print(f"[OK] Alarm siren written to : {path}")
    print(f"     File size              : {size:,} bytes "
          f"({size / 1024:.1f} KiB, about {size * 4 / 3 / 1024:.0f} KiB when base64-encoded)")
    print(f"     Sample rate / duration : {SAMPLE_RATE} Hz / {DURATION_S:.2f} s")
    print(f"     Copy for external play : {ALARM_WAV_COPY}")
