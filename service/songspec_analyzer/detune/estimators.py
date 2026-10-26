"""Detune estimators. Centsible (the C++ core, via _centsible) is the primary one; the others are independent cross-checks that only
matter when Centsible itself is unsure (wide interval, competing tunings). Each returns cents on [-50, 50) and a confidence 0..1."""
import numpy as np

SR = 22050


def wrap(c):
    return c - 100.0 * np.floor((c + 50.0) / 100.0)


def circ_mean(cents, w):
    a = 2 * np.pi * np.asarray(cents) / 100.0
    w = np.asarray(w, float)
    z = np.sum(w * np.exp(1j * a)) / max(np.sum(w), 1e-12)
    return float(wrap(np.angle(z) * 100.0 / (2 * np.pi))), float(np.abs(z))


def circ_wmedian(values, weights):
    """Weighted median on the 100-cent circle: unwrap around the circular mean, then an ordinary weighted median."""
    m, _ = circ_mean(values, weights)
    rel = wrap(np.asarray(values) - m)
    o = np.argsort(rel)
    cw = np.cumsum(np.asarray(weights, float)[o])
    return float(wrap(m + rel[o][np.searchsorted(cw, 0.5 * cw[-1])]))


def harmonic_22k(y, sr):
    import librosa
    y22 = librosa.resample(y, orig_sr=sr, target_sr=SR) if sr != SR else y
    return librosa.effects.harmonic(y22, margin=2.0)


def chroma120(yh):
    """CQT at 120 bins per octave (10 cents per bin) over C3-C7 on the harmonic part. Every spectral peak (local maximum over
    +-2 bins, within 30 dB of the frame maximum) is located to a fraction of a bin by parabolic interpolation; its offset from the
    12-TET grid is folded onto one semitone and the energy-weighted circular mean is the tuning."""
    import librosa
    C = np.abs(librosa.cqt(yh, sr=SR, hop_length=2048, fmin=librosa.note_to_hz("C3"), n_bins=480, bins_per_octave=120,
                           tuning=0.0))
    L = np.log(C + 1e-10)
    cents, w = [], []
    for f in range(C.shape[1]):
        col, lcol = C[:, f], L[:, f]
        top = col.max()
        if top <= 1e-6:
            continue
        k = np.arange(2, len(col) - 2)
        pk = k[(col[k] >= col[k - 1]) & (col[k] >= col[k + 1]) & (col[k] >= col[k - 2]) & (col[k] >= col[k + 2]) & (col[k] > top * 0.03)]
        if not len(pk):
            continue
        a, b, c = lcol[pk - 1], lcol[pk], lcol[pk + 1]
        den = a - 2 * b + c
        frac = np.where(np.abs(den) > 1e-9, 0.5 * (a - c) / den, 0.0).clip(-0.5, 0.5)
        cents.extend(wrap((pk + frac) * 10.0))
        w.extend(col[pk] ** 2)
    if len(cents) < 50:
        return None, 0.0
    m, R = circ_mean(cents, w)
    return m, R


def librosa_tuning(yh):
    """librosa.estimate_tuning (piptrack + pitch_tuning) on the harmonic part, 0.1-cent resolution."""
    import librosa
    t = librosa.estimate_tuning(y=yh, sr=SR, resolution=0.001, bins_per_octave=12)
    return float(wrap(100.0 * t)), 0.5
