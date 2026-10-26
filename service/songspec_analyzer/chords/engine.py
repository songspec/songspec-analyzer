"""Chord recognition, beat-synchronous (our own code; BTC / Chordino can join as extra votes once their use is approved).

1. Features: CQT (36 bins / octave, C1-C8) of the harmonic signal, MEASURED AGAINST THE SONG'S TUNING; folded into a treble chroma
   (C3-C8) and a bass chroma (C1-B2); one value per beat (median over the beat).
2. Emission per beat and chord: cosine of the treble chroma with the chord's template + agreement of the bass with the chord's root,
   plus a log-prior per quality (triads unless an extension is clearly there). 'N' (no chord) wins on quiet / flat beats.
3. Viterbi over beats: staying costs nothing, changing costs CHANGE on a downbeat, more on another beat (so a one-beat chord needs
   strong evidence on both sides).
4. Segments, slash chords from the bass, confidence = mean posterior of the chosen chord.
"""
import numpy as np

from .vocab import FLAT, TRIAD_OF, split, templates

SR = 22050
HOP = 512
BINS = 36
TEMP = 0.07            # softmax temperature over cosine scores (posteriors for confidence)
BASS_W = 0.25          # weight of the bass evidence: 0.6 x "the bass plays the root" + 0.4 x "the bass plays a chord tone"
CHANGE_DOWNBEAT = 1.5  # log-cost of a chord change on a downbeat
CHANGE_BEAT = 4.0      # ... on any other beat
N_ENERGY = 0.08        # beats quieter than this fraction of the median beat energy are 'N'
PRIOR_SCALE = 3.0      # how strongly the quality priors keep the decoder on triads


def cqt_chromas(yh, tuning_cents):
    """(treble chroma 12 x T, bass chroma 12 x T, energy T) at HOP, against the song's tuning."""
    import librosa
    C = np.abs(librosa.cqt(yh, sr=SR, hop_length=HOP, fmin=librosa.note_to_hz("C1"), n_bins=BINS * 7, bins_per_octave=BINS,
                           tuning=float(tuning_cents) / 100.0))
    L = np.log1p(100.0 * C / (C.max() + 1e-12))
    pc = ((np.arange(L.shape[0]) + 1) // 3) % 12                        # bins 3s-1, 3s, 3s+1 belong to semitone s
    bass_rows = np.arange(L.shape[0]) < BINS * 2                        # C1 .. B2
    treble = np.zeros((12, L.shape[1]))
    bass = np.zeros((12, L.shape[1]))
    for s in range(12):
        treble[s] = L[(pc == s) & ~bass_rows].sum(axis=0)
        bass[s] = L[(pc == s) & bass_rows].sum(axis=0)
    return treble, bass, C.sum(axis=0)


def beat_sync(X, beats, n_frames):
    import librosa
    fr = librosa.time_to_frames(np.asarray(beats), sr=SR, hop_length=HOP)
    fr = np.unique(fr[(fr >= 0) & (fr < n_frames)])
    return librosa.util.sync(X, fr, aggregate=np.median, pad=False), fr


def discount_bass_harmonics(treble_b, bass_b):
    """A bass note's 3rd harmonic lands a fifth above it in the treble (E2 -> B3) and its 2nd / 4th on the note itself: without this an
    inversion like C/E reads as Em. Per beat, the dominant bass pitch class (scaled by how clearly it dominates) lowers the treble at
    its fifth by up to 50 % and at itself by up to 30 %."""
    t = treble_b.copy()
    bn = bass_b / (bass_b.max(axis=0, keepdims=True) + 1e-12)
    pb = np.argmax(bn, axis=0)
    clear = np.clip((1.0 - np.median(bn, axis=0)) * 1.5, 0.0, 1.0)
    cols = np.arange(t.shape[1])
    t[(pb + 7) % 12, cols] *= 1.0 - 0.5 * clear
    t[pb, cols] *= 1.0 - 0.3 * clear
    return t


def emissions(treble_b, bass_b, energy_b, vocab="full"):
    labs, T, prior = templates(vocab)
    treble_b = discount_bass_harmonics(treble_b, bass_b)
    tn = treble_b / (np.linalg.norm(treble_b, axis=0, keepdims=True) + 1e-12)
    cos = T @ tn                                                          # K x B
    from .vocab import QUALITIES
    bn = bass_b / (bass_b.max(axis=0, keepdims=True) + 1e-12)
    bass_term = np.zeros_like(cos)
    for k, lab in enumerate(labs[1:], 1):
        root, q = split(lab)
        tones = [(root + i) % 12 for i in QUALITIES[q][0]]
        # an inversion (C/E) keeps its bass support through the chord-tone term; the root term separates relative chords (Am / C)
        bass_term[k] = 0.6 * bn[root] + 0.4 * bn[tones].max(axis=0)
    score = cos + BASS_W * bass_term
    score[0] = cos[0] - 0.15                                              # N: the flat template, slightly penalised
    quiet = energy_b < N_ENERGY * np.median(energy_b[energy_b > 0]) if (energy_b > 0).any() else np.ones_like(energy_b, bool)
    score[0, quiet] += 1.0
    # quality priors in nats (vocab.py: 0 for triads, -0.6 .. -1.3 for extensions) x PRIOR_SCALE: every note's 3rd harmonic adds
    # its fifth to the chroma (C -> G), which alone would make plain triads read as maj7 / add9
    logp = score / TEMP + prior[:, None] * PRIOR_SCALE
    post = np.exp(logp - logp.max(axis=0, keepdims=True))
    post /= post.sum(axis=0, keepdims=True)
    return labs, logp, post


def viterbi(logp, downbeat):
    """logp: K x B; downbeat: bool per beat. Returns the best label index per beat."""
    K, B = logp.shape
    delta = logp[:, 0].copy()
    back = np.zeros((K, B), int)
    for b in range(1, B):
        cost = CHANGE_DOWNBEAT if downbeat[b] else CHANGE_BEAT
        best_prev = int(np.argmax(delta))
        stay = delta
        move = delta[best_prev] - cost
        take_move = move > stay
        back[:, b] = np.where(take_move, best_prev, np.arange(K))
        delta = np.where(take_move, move, stay) + logp[:, b]
    path = np.zeros(B, int)
    path[-1] = int(np.argmax(delta))
    for b in range(B - 1, 0, -1):
        path[b - 1] = back[path[b], b]
    return path


def slash(root, quality, bass_seg):
    """'/X' when the bass clearly sits on a chord tone other than the root (an inversion)."""
    from .vocab import QUALITIES
    if root is None:
        return None
    b = int(np.argmax(bass_seg))
    tones = {(root + i) % 12 for i in QUALITIES[quality][0]}
    if b != root and b in tones and bass_seg[b] > 1.3 * bass_seg[root]:
        return b
    return None


def _smooth_bass(beat_bass, path):
    """Within a run of the same chord, a slash bass must hold for 2+ beats (a passing bass note is not an inversion)."""
    out = list(beat_bass)
    i = 0
    while i < len(path):
        j = i
        while j + 1 < len(path) and path[j + 1] == path[i]:
            j += 1
        k = i
        while k <= j:
            m = k
            while m + 1 <= j and out[m + 1] == out[k]:
                m += 1
            if out[k] is not None and m - k + 1 < 2:
                for x in range(k, m + 1):
                    out[x] = None
            k = m + 1
        i = j + 1
    return out


def recognise(yh, tuning_cents, beats, downbeats, duration, vocab="full"):
    """-> list of segments {start, end, chord, triad, root, quality, bass, conf, on_downbeat}."""
    beats = np.asarray(beats, float)
    if len(beats) < 4:
        return []
    treble, bass, energy = cqt_chromas(yh, tuning_cents)
    edges = np.concatenate([beats, [min(duration, beats[-1] + np.median(np.diff(beats)))]])
    tb, fr = beat_sync(treble, edges, treble.shape[1])
    bb, _ = beat_sync(bass, edges, bass.shape[1])
    eb, _ = beat_sync(energy[None, :], edges, energy.shape[0])
    import librosa
    t_frames = librosa.frames_to_time(fr, sr=SR, hop_length=HOP)
    starts = t_frames[:-1] if len(t_frames) > tb.shape[1] else t_frames
    tb, bb, eb = tb[:, :len(starts)], bb[:, :len(starts)], eb[0, :len(starts)]
    labs, logp, post = emissions(tb, bb, eb, vocab)
    db = np.asarray(downbeats if downbeats is not None else [], float)
    is_down = np.array([db.size > 0 and np.min(np.abs(db - s)) < 0.1 for s in starts])
    path = viterbi(logp, is_down)
    # the bass of every beat (an inversion is part of the label, so C then C/E are two segments)
    beat_bass = []
    for b, k in enumerate(path):
        root, q = split(labs[k])
        beat_bass.append(slash(root, q, bb[:, b]) if root is not None else None)
    beat_bass = _smooth_bass(beat_bass, path)
    segs = []
    i = 0
    while i < len(path):
        j = i
        while j + 1 < len(path) and path[j + 1] == path[i] and beat_bass[j + 1] == beat_bass[i]:
            j += 1
        lab = labs[path[i]]
        root, q = split(lab)
        bs = beat_bass[i]
        name = lab if bs is None else f"{lab}/{FLAT[bs]}"
        end = starts[j + 1] if j + 1 < len(starts) else edges[-1]
        segs.append({"start": round(float(starts[i]), 3), "end": round(float(end), 3), "chord": name,
                     "triad": "N" if root is None else FLAT[root] + TRIAD_OF[q], "root": root, "quality": q,
                     "bass": None if root is None else FLAT[bs if bs is not None else root],
                     "conf": round(float(post[path[i], i:j + 1].mean()), 3), "on_downbeat": bool(is_down[i])})
        i = j + 1
    return segs
