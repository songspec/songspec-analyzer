// Python binding of Centsible's analysis core (core/ is a verbatim copy of Centsible Source/core, the code the VST3 plugin and
// centsible_cli run). Nothing here changes the DSP: it calls the same entry points and converts the results to Python dicts.
//
//   analyze_track(mono, sr)            -> centsible::analyzeTrack: tuning (the plugin's reading), key, tempo
//   key_probabilities(mono, sr, cents) -> the key detector's full decision: 24 key probabilities, 7 mode probabilities, features
//   detune_curve(mono, sr, window, hop)-> detune over time (detune_timeline.h, the curve Beatswitch draws)
//
// The GIL is released while the C++ runs, so several analyses can run on Python worker threads.
#include <pybind11/numpy.h>
#include <pybind11/pybind11.h>
#include <pybind11/stl.h>

#include <centsible/track.h>
#include "detune_timeline.h"
#include "key_detector.h"
#include "track_internal.h"

namespace py = pybind11;
using F32 = py::array_t<float, py::array::c_style | py::array::forcecast>;

namespace
{
std::vector<float> mono (const F32& x)
{
    if (x.ndim() != 1) throw std::invalid_argument ("expected a 1-D float32 array of mono samples");
    return std::vector<float> (x.data(), x.data() + x.size());
}

template <std::size_t N> py::list toList (const std::array<double, N>& a) { py::list l; for (double v : a) l.append (v); return l; }
template <std::size_t N> py::list toList (const std::array<float, N>& a) { py::list l; for (float v : a) l.append ((double) v); return l; }

py::dict tuningDict (const centsible::Reading& r)
{
    py::dict d;
    d["valid"] = r.valid; d["cents"] = (double) r.cents; d["ref_hz"] = (double) r.refHz; d["ci_cents"] = (double) r.ciCents;
    d["confidence"] = (double) r.confidence; d["locked"] = r.locked; d["settled"] = r.settled; d["seconds"] = (double) r.seconds;
    d["competing"] = r.competing; d["contested"] = r.contested; d["song_changes"] = r.songChanges;
    d["dominance"] = (double) r.dominance; d["notes"] = r.notes;
    d["has_secondary"] = r.hasSecondary; d["secondary_cents"] = (double) r.secondaryCents; d["secondary_share"] = (double) r.secondaryShare;
    d["has_alt"] = r.hasAlt; d["alt_cents"] = (double) r.altCents;
    d["has_drift"] = r.hasDrift; d["drift_cents"] = (double) r.driftCents;
    return d;
}

py::dict keyDict (const centsible::KeyEstimate& k)
{
    py::dict d;
    d["valid"] = k.valid; d["root"] = k.root; d["minor"] = k.minor; d["confidence"] = (double) k.confidence;
    d["scale_root"] = k.scaleRoot; d["scale_confidence"] = (double) k.scaleConfidence; d["degree"] = k.degree;
    d["between_keys"] = k.betweenKeys; d["probability"] = (double) k.probability;
    d["runner_up_root"] = k.runnerUpRoot; d["runner_up_minor"] = k.runnerUpMinor; d["runner_up_probability"] = (double) k.runnerUpProbability;
    d["relative_probability"] = (double) k.relativeProbability; d["relative_ambiguous"] = k.relativeAmbiguous;
    d["mode_probability"] = toList (k.modeProbability);
    if (k.valid) { d["label"] = centsible::keyLabel (k); d["detail"] = centsible::keyDetail (k); d["camelot"] = centsible::camelot (k.root, k.minor); }
    return d;
}
} // namespace

PYBIND11_MODULE (_centsible, m)
{
    m.doc() = "Centsible analysis core (tuning / key / tempo), the same code as the Centsible VST3 plugin and centsible_cli";
    m.attr("CORE_VERSION") = "1.3.0";

    m.def ("analyze_track", [] (const F32& x, double sr)
    {
        auto v = mono (x);
        centsible::TrackAnalysis a;
        {
            py::gil_scoped_release nogil;
            a = centsible::analyzeTrack (v.data(), v.size(), sr);
        }
        py::dict d;
        d["tuning"] = tuningDict (a.tuning);
        d["key"] = keyDict (a.key);
        py::dict t;
        t["valid"] = a.tempo.valid; t["bpm"] = (double) a.tempo.bpm; t["confidence"] = (double) a.tempo.confidence; t["alt_bpm"] = (double) a.tempo.altBpm;
        d["tempo"] = t;
        d["seconds"] = a.seconds;
        return d;
    }, py::arg ("mono"), py::arg ("sample_rate"), "centsible::analyzeTrack (whole-track tuning, key, tempo)");

    m.def ("analyze_tuning", [] (const F32& x, double sr)
    {
        // the tuning half of analyzeTrack, exactly as it runs there (Analyzer fed in 4096-sample blocks; the estimator works hop by
        // hop, so the block size does not change the reading), without the key and tempo passes the service does elsewhere
        auto v = mono (x);
        centsible::Reading rd;
        {
            py::gil_scoped_release nogil;
            centsible::Analyzer analyzer;
            analyzer.prepare (sr);
            for (std::size_t pos = 0; pos < v.size(); pos += 4096)
                analyzer.push (v.data() + pos, (int) std::min<std::size_t> (4096, v.size() - pos));
            rd = analyzer.reading();
        }
        return tuningDict (rd);
    }, py::arg ("mono"), py::arg ("sample_rate"), "the plugin's tuning reading only (centsible::Analyzer over the whole track)");

    m.def ("key_probabilities", [] (const F32& x, double sr, double tuningCents)
    {
        // exactly what estimateKey (track_features.cpp) computes, returning the whole decision instead of the summary
        auto v = mono (x);
        centsible::keydetail::KeyFeatures f;
        centsible::keydetail::KeyDecision dcs;
        {
            py::gil_scoped_release nogil;
            centsible::keydetail::KeyFeatureOptions opt;
            opt.hpss = true;
            f = centsible::keydetail::computeKeyFeatures (v.data(), v.size(), sr, tuningCents, opt);
            // the shipped weights (track_features.cpp, estimateKey); tests/test_centsible.py checks the decision matches analyze_track
            centsible::keydetail::KeyModel model;
            model.wBass = 0.8435; model.wNotes = 1.4350; model.wMajorMinor = 0.1705;
            model.degreeBias = { 1.3333, -0.8154, 0.2550, -1.3267, -0.4802, 1.6067, -0.5726 };
            dcs = centsible::keydetail::decideKey (f, model);
        }
        py::dict d;
        d["valid"] = dcs.valid; d["tonic"] = dcs.tonic; d["minor"] = dcs.minor; d["scale"] = dcs.scale; d["degree"] = dcs.degree;
        d["scale_confidence"] = dcs.scaleConfidence; d["tonic_confidence"] = dcs.tonicConfidence;
        d["key_prob"] = toList (dcs.keyProb); d["mode_prob"] = toList (dcs.modeProb); d["profile_scores"] = toList (dcs.profileScores);
        d["chroma_scale"] = toList (f.peaksGated); d["chroma_bass"] = toList (f.cancBass); d["chroma_notes"] = toList (f.cancGated);
        d["frames"] = f.frames;
        return d;
    }, py::arg ("mono"), py::arg ("sample_rate"), py::arg ("tuning_cents"));

    m.def ("detune_curve", [] (const F32& x, double sr, double window, double hop)
    {
        auto v = mono (x);
        std::vector<centsible::detail::DetunePoint> pts;
        {
            py::gil_scoped_release nogil;
            const auto fe = centsible::detail::recordFrames (v.data(), v.size(), sr);
            centsible::detail::DetuneOptions opt;
            opt.windowSeconds = window;
            opt.hopSeconds = hop;
            const auto curve = centsible::detail::computeDetuneCurve (fe, 0.0, fe.seconds, opt);
            pts = curve.points;
        }
        py::list l;
        for (const auto& p : pts)
        {
            py::dict d;
            d["t"] = p.time; d["cents"] = p.cents; d["ci"] = p.ci; d["mass"] = p.mass; d["sharpness"] = p.sharpness;
            d["window"] = p.window; d["valid"] = p.valid;
            l.append (d);
        }
        return l;
    }, py::arg ("mono"), py::arg ("sample_rate"), py::arg ("window") = 3.0, py::arg ("hop") = 1.0);
}
