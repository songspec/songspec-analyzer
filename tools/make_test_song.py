#!/usr/bin/env python3
"""A 32 s synthetic test song for smoke tests on machines without the catalog (the macOS CI build): C major, I-V-vi-IV, one chord
per bar at 120 BPM in 4/4, with a kick on every beat. The analyzer should say C Major, 120 BPM, 4/4, about 0 cents.
Standard library only.   python3 tools/make_test_song.py out.wav
"""
import math
import struct
import sys
import wave

SR, BPM, BARS = 44100, 120.0, 16
CHORDS = [(60, 64, 67), (55, 59, 62), (57, 60, 64), (53, 57, 60)]  # C G Am F


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


def main(path):
    beat = 60.0 / BPM
    n = int(SR * beat * 4 * BARS)
    out = [0.0] * n
    for bar in range(BARS):
        t0 = bar * 4 * beat
        notes = CHORDS[bar % 4]
        bass = notes[0] - 24
        s0, s1 = int(t0 * SR), int((t0 + 4 * beat) * SR)
        for i in range(s0, min(s1, n)):
            t = (i - s0) / SR
            env = min(1.0, t * 50) * math.exp(-t * 0.6)
            v = sum(math.sin(2 * math.pi * hz(m) * t) + 0.3 * math.sin(4 * math.pi * hz(m) * t) for m in notes) / 6
            v += 0.5 * math.sin(2 * math.pi * hz(bass) * t)
            out[i] += 0.35 * env * v
        for b in range(4):  # kick: a pitch-dropping sine burst on each beat
            k0 = int((t0 + b * beat) * SR)
            for i in range(int(0.15 * SR)):
                if k0 + i >= n:
                    break
                t = i / SR
                f = 50 + 100 * math.exp(-t * 30)
                out[k0 + i] += (0.9 if b == 0 else 0.7) * math.exp(-t * 25) * math.sin(2 * math.pi * f * t)
    peak = max(abs(x) for x in out) or 1.0
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(b"".join(struct.pack("<h", int(32000 * x / peak)) for x in out))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "test_song.wav")
