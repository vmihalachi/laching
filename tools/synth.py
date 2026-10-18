#!/usr/bin/env python3
"""Writes a companion's sounds: short original tones synthesized from numbers, so nothing is sampled or borrowed.

    python3 laching/tools/synth.py            # writes <sounds.out>/*.wav
    python3 laching/tools/synth.py --check    # exit 1 when a committed file differs

The app's recipes live in its own module (`sounds.recipes` in companion.json), which defines
`sounds(engine) -> {name: samples}` built from this engine's `tone`, `glide` and `silence` (and may set `PEAK`):

    def sounds(engine):
        return {"blip": engine.tone(1318.5, 0.15, gain=0.8, warmth=0.1)}

Deterministic (no randomness, pure arithmetic in Python's double precision), 44.1 kHz, 16-bit mono WAV. Standard
library only.
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import math
import struct
import sys
import wave
from pathlib import Path
from types import ModuleType

RATE = 44_100
PEAK = 0.45  # headroom: these play over speech and system sounds, so they stay gentle

# A pentatonic scale around A4 keeps every tune friendly and original.
C5, D5, E5, G5, A5, C6, E6, A4, G4 = 523.25, 587.33, 659.25, 783.99, 880.0, 1046.5, 1318.5, 440.0, 392.0


def envelope(i: int, n: int, attack: float = 0.012, release: float = 0.08) -> float:
    t = i / RATE
    remaining = (n - i) / RATE
    a = min(1.0, t / attack)
    r = min(1.0, remaining / release)
    return a * r


def tone(freq: float, seconds: float, gain: float = 1.0, warmth: float = 0.25, vibrato: float = 0.0) -> list[float]:
    """A sine with a touch of the second harmonic (`warmth`): round, a bit like a hummed note."""
    n = int(seconds * RATE)
    out = []
    for i in range(n):
        t = i / RATE
        f = freq * (1 + vibrato * math.sin(2 * math.pi * 5.5 * t))
        phase = 2 * math.pi * f * t
        s = math.sin(phase) + warmth * math.sin(2 * phase)
        out.append(gain * envelope(i, n) * s / (1 + warmth))
    return out


def silence(seconds: float) -> list[float]:
    return [0.0] * int(seconds * RATE)


def glide(f0: float, f1: float, seconds: float, gain: float = 1.0) -> list[float]:
    """A note sliding from `f0` to `f1`."""
    n = int(seconds * RATE)
    out, phase = [], 0.0
    for i in range(n):
        f = f0 + (f1 - f0) * (i / n)
        phase += 2 * math.pi * f / RATE
        out.append(gain * envelope(i, n, release=0.15) * (math.sin(phase) + 0.2 * math.sin(2 * phase)) / 1.2)
    return out


def pcm(samples: list[float], peak: float = PEAK) -> bytes:
    return b"".join(struct.pack("<h", int(max(-1.0, min(1.0, s * peak)) * 32767)) for s in samples)


def wav_bytes(samples: list[float], peak: float = PEAK) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm(samples, peak))
    return buffer.getvalue()


def load_recipes(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("companion_sounds", path)
    if spec is None or spec.loader is None:
        sys.exit(f"synth: can't load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "sounds"):
        sys.exit(f"synth: {path} defines no sounds(engine)")
    return module


def render(recipes: ModuleType) -> dict[str, bytes]:
    """Each sound's WAV bytes."""
    peak = getattr(recipes, "PEAK", PEAK)
    return {name: wav_bytes(samples, peak) for name, samples in recipes.sounds(sys.modules[__name__]).items()}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="write nothing; exit 1 when a committed file differs")
    check = parser.parse_args(argv).check
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import companion

    config = companion.load()
    out = config.require_path("sounds.out")
    recipes = load_recipes(config.require_path("sounds.recipes"))
    files = {f"{name}.wav": data for name, data in render(recipes).items()}
    changed = companion.write_outputs(files, check, out)
    for path in changed:
        if check:
            print(f"stale: {companion.rel(path, config.root)} (run laching/tools/synth.py)")
        else:
            print(f"wrote {companion.rel(path, config.root)} ({(len(files[path.name]) - 44) / 2 / RATE:.2f} s)")
    return 1 if changed and check else 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):  # UTF-8 on a cp1252 Windows console too (companion.utf8_console)
        stream.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main(sys.argv[1:]))
