"""Render every buzzer pattern to a WAV file, to hear them without the hardware.

    python3 scripts/buzzer_samples.py [OUTPUT_DIR] [--frequency 2700]

Writes one file per pattern plus all-patterns.wav (each pattern in turn with
a pause between). The tone imitates a typical 2.7 kHz active buzzer: a square
wave with the edges slightly rounded so it's easier on the ears.
"""

from __future__ import annotations

import argparse
import math
import struct
import sys
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from nfc_login.hardware.buzzer import PATTERNS  # noqa: E402

RATE = 44_100
RAMP = 0.003          # seconds of fade in/out on each beep (avoids clicks)
VOLUME = 0.35


def tone(seconds: float, frequency: float) -> list[float]:
    n = int(seconds * RATE)
    ramp = max(1, int(RAMP * RATE))
    out = []
    for i in range(n):
        t = i / RATE
        # Odd harmonics up to the 5th: buzzer-like, not as harsh as a pure square.
        s = sum(math.sin(2 * math.pi * frequency * k * t) / k for k in (1, 3, 5))
        env = min(1.0, i / ramp, (n - i) / ramp)
        out.append(s * env * VOLUME)
    return out


def silence(seconds: float) -> list[float]:
    return [0.0] * int(seconds * RATE)


def render(pattern: list[int], frequency: float) -> list[float]:
    samples = silence(0.15)
    for i, ms in enumerate(pattern):
        samples += tone(ms / 1000, frequency) if i % 2 == 0 else silence(ms / 1000)
    return samples + silence(0.25)


def write(path: Path, samples: list[float]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767))
                               for s in samples))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", nargs="?", default="buzzer-samples")
    parser.add_argument("--frequency", type=float, default=2700)
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    everything: list[float] = []
    for name, pattern in PATTERNS.items():
        samples = render(pattern, args.frequency)
        write(out / f"{name}.wav", samples)
        everything += samples + silence(0.5)
        print(f"{name}.wav  {pattern}")
    write(out / "all-patterns.wav", everything)
    print(f"all-patterns.wav  (every pattern in order) -> {out}/")


if __name__ == "__main__":
    main()
