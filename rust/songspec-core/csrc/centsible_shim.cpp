// C ABI over Centsible's analysis core for the Rust engine. The core itself is compiled from service/native/centsible/core (the
// verbatim copy of Centsible Source/core the Python service binds), so the desktop app runs the same DSP. Each function mirrors
// one entry point of service/native/centsible/binding.cpp exactly (same options, same shipped key-model weights).
#include <algorithm>
#include <cstddef>

#include <centsible/analyzer.h>
#include "detune_timeline.h"
#include "key_detector.h"

extern "C"
{
struct SsTuning
{
    int valid, locked, settled, competing, contested, song_changes, notes, has_secondary, has_alt, has_drift;
    double cents, ref_hz, ci_cents, confidence, seconds, dominance, secondary_cents, secondary_share, alt_cents, drift_cents;
};

struct SsKey
{
    int valid, tonic, minor, scale, degree, frames;
    double scale_confidence, tonic_confidence;
    double key_prob[24], mode_prob[7], profile_scores[24];
    double chroma_scale[12], chroma_bass[12], chroma_notes[12];
};

struct SsDetunePoint
{
    double t, cents, ci, mass, sharpness, window;
    int valid;
};

// binding.cpp analyze_tuning: centsible::Analyzer fed in 4096-sample blocks over the whole track
void ss_analyze_tuning (const float* x, std::size_t n, double sr, SsTuning* out)
{
    centsible::Analyzer analyzer;
    analyzer.prepare (sr);
    for (std::size_t pos = 0; pos < n; pos += 4096)
        analyzer.push (x + pos, (int) std::min<std::size_t> (4096, n - pos));
    const centsible::Reading& r = analyzer.reading();
    out->valid = r.valid; out->cents = r.cents; out->ref_hz = r.refHz; out->ci_cents = r.ciCents;
    out->confidence = r.confidence; out->locked = r.locked; out->settled = r.settled; out->seconds = r.seconds;
    out->competing = r.competing; out->contested = r.contested; out->song_changes = r.songChanges;
    out->dominance = r.dominance; out->notes = r.notes;
    out->has_secondary = r.hasSecondary; out->secondary_cents = r.secondaryCents; out->secondary_share = r.secondaryShare;
    out->has_alt = r.hasAlt; out->alt_cents = r.altCents;
    out->has_drift = r.hasDrift; out->drift_cents = r.driftCents;
}

// binding.cpp key_probabilities: computeKeyFeatures (hpss) + decideKey with the shipped weights (track_features.cpp estimateKey)
void ss_key_probabilities (const float* x, std::size_t n, double sr, double tuningCents, SsKey* out)
{
    centsible::keydetail::KeyFeatureOptions opt;
    opt.hpss = true;
    const auto f = centsible::keydetail::computeKeyFeatures (x, n, sr, tuningCents, opt);
    centsible::keydetail::KeyModel model;
    model.wBass = 0.8435; model.wNotes = 1.4350; model.wMajorMinor = 0.1705;
    model.degreeBias = { 1.3333, -0.8154, 0.2550, -1.3267, -0.4802, 1.6067, -0.5726 };
    const auto d = centsible::keydetail::decideKey (f, model);
    out->valid = d.valid; out->tonic = d.tonic; out->minor = d.minor; out->scale = d.scale; out->degree = d.degree;
    out->scale_confidence = d.scaleConfidence; out->tonic_confidence = d.tonicConfidence; out->frames = f.frames;
    for (int i = 0; i < 24; ++i) { out->key_prob[i] = d.keyProb[(std::size_t) i]; out->profile_scores[i] = d.profileScores[(std::size_t) i]; }
    for (int i = 0; i < 7; ++i) out->mode_prob[i] = d.modeProb[(std::size_t) i];
    for (int i = 0; i < 12; ++i)
    {
        out->chroma_scale[i] = f.peaksGated[(std::size_t) i];
        out->chroma_bass[i] = f.cancBass[(std::size_t) i];
        out->chroma_notes[i] = f.cancGated[(std::size_t) i];
    }
}

// binding.cpp detune_curve: recordFrames + computeDetuneCurve over the whole track. Returns the number of points written (at most
// cap); call with cap 0 to get the count.
std::size_t ss_detune_curve (const float* x, std::size_t n, double sr, double window, double hop, SsDetunePoint* out, std::size_t cap)
{
    const auto fe = centsible::detail::recordFrames (x, n, sr);
    centsible::detail::DetuneOptions opt;
    opt.windowSeconds = window;
    opt.hopSeconds = hop;
    const auto curve = centsible::detail::computeDetuneCurve (fe, 0.0, fe.seconds, opt);
    const std::size_t m = std::min (cap, curve.points.size());
    for (std::size_t i = 0; i < m; ++i)
    {
        const auto& p = curve.points[i];
        out[i] = { p.time, p.cents, p.ci, p.mass, p.sharpness, p.window, p.valid ? 1 : 0 };
    }
    return cap == 0 ? curve.points.size() : m;
}
}
