"""Key fusion: a rotation-equivariant multinomial logistic regression over the 24 keys.

For every candidate key k (tonic t, major or minor) the SAME features are computed relative to k:
  - each estimator's log-probability of k (clipped at 1e-4)
  - tiebreakers for the classic confusions (PLAN.md 3.4):
      bass_tonic       bass chroma at t                         (relative / modal: the tonic dominates the bass)
      bass_fifth       bass chroma at t + 7                     (fifth errors: V in the bass is common, I more so)
      leading_tone     chroma at t + 11 minus at t + 10         (fifth errors: G major has F#, C major does not)
      third            chroma at the candidate's third minus the other third   (parallel C vs Cm)
      triad            chroma share of the candidate's tonic triad
      ends             how well the first and last 8 beats fit the tonic triad  (songs start / end at home)
score(k) = w . f(k) + b_mode(minor?); P = softmax over the 24 candidates. One weight vector for all keys, so a few hundred labelled
songs suffice. Trained by L2-regularised maximum likelihood (scipy L-BFGS); the temperature is folded into w.
"""
import numpy as np

FEATURES = ["bass_tonic", "bass_fifth", "leading_tone", "third", "triad", "ends"]


def _norm(v):
    v = np.asarray(v, float)
    s = v.sum()
    return v / s if s > 0 else np.full_like(v, 1 / len(v))


def candidate_features(chroma, bass, seq=None):
    """24 x len(FEATURES) tiebreaker features. chroma: 12 (global, tuned), bass: 12, seq: beats x 12 (optional)."""
    c, b = _norm(chroma), _norm(bass)
    ends = np.zeros(24)
    if seq is not None and len(seq) >= 16:
        S = np.asarray(seq, float)
        edge = _norm(S[:8].sum(axis=0)) + _norm(S[-8:].sum(axis=0))
    else:
        edge = None
    F = np.zeros((24, len(FEATURES)))
    for k in range(24):
        t, minor = k % 12, k >= 12
        third, other = ((t + 3) % 12, (t + 4) % 12) if minor else ((t + 4) % 12, (t + 3) % 12)
        triad = [t, third, (t + 7) % 12]
        F[k, 0] = b[t]
        F[k, 1] = b[(t + 7) % 12]
        F[k, 2] = c[(t + 11) % 12] - c[(t + 10) % 12]
        F[k, 3] = c[third] - c[other]
        F[k, 4] = c[triad].sum()
        if edge is not None:
            ends[k] = edge[triad].sum() / 2.0
        F[k, 5] = ends[k]
    return F


def design(est_probs, feats, names):
    """est_probs: {name: 24 probs}; feats: 24 x F. -> 24 x (len(names) + F + 1) design matrix (last column: minor indicator)."""
    cols = [np.log(np.clip(np.asarray(est_probs[n], float), 1e-4, 1.0)) for n in names]
    X = np.column_stack(cols + [feats, np.r_[np.zeros(12), np.ones(12)]])
    return X


FEATURE_KINDS = ("none", "tiebreak", "chords", "tiebreak+chords")


def feature_matrix(kind, tiebreak, chords):
    """The candidate feature columns for a feature kind: tiebreak (24 x 6), chords (24 x 5, key/chordkey.py)."""
    if kind == "none":
        return np.zeros((24, 0))
    if kind == "tiebreak":
        return np.asarray(tiebreak, float)
    if kind == "chords":
        return np.asarray(chords, float)
    return np.hstack([np.asarray(tiebreak, float), np.asarray(chords, float)])


class KeyFusion:
    def __init__(self, names, l2=1.0, use_features=True, feature_kind=None):
        self.names, self.l2, self.use_features = list(names), l2, use_features
        self.feature_kind = feature_kind or ("tiebreak" if use_features else "none")
        self.w = None

    def _X(self, est_probs, feats):
        X = design(est_probs, feats, self.names)
        if not self.use_features:
            X = np.column_stack([X[:, :len(self.names)], X[:, -1]])
        return X

    def fit(self, samples):
        """samples: [(est_probs, feats, true_index)]"""
        from scipy.optimize import minimize
        X3 = np.stack([self._X(p, f) for p, f, _ in samples])          # songs x 24 candidates x d
        ys = np.asarray([y for *_, y in samples])
        n, d, idx = len(ys), X3.shape[2], np.arange(len(ys))
        # the estimator columns start from equal weights (a plain product of experts), features from 0
        w0 = np.zeros(d)
        w0[:len(self.names)] = 1.0 / max(1, len(self.names))

        def loss(w):
            s = X3 @ w
            s -= s.max(axis=1, keepdims=True)
            p = np.exp(s)
            p /= p.sum(axis=1, keepdims=True)
            L = -np.log(p[idx, ys] + 1e-12).sum()
            g = -(X3[idx, ys] - np.einsum("nk,nkd->nd", p, X3)).sum(axis=0)
            reg = self.l2 * np.sum((w - w0) ** 2)
            return L / n + reg, g / n + 2 * self.l2 * (w - w0)

        self.w = minimize(loss, w0, jac=True, method="L-BFGS-B").x
        return self

    def predict_proba(self, est_probs, feats):
        s = self._X(est_probs, feats) @ self.w
        s -= s.max()
        p = np.exp(s)
        return p / p.sum()

    def to_dict(self):
        return {"names": self.names, "l2": self.l2, "use_features": self.use_features, "feature_kind": self.feature_kind,
                "features": FEATURES, "w": [round(float(x), 6) for x in self.w]}

    @classmethod
    def from_dict(cls, d):
        m = cls(d["names"], d["l2"], d["use_features"], d.get("feature_kind"))
        m.w = np.asarray(d["w"], float)
        return m
