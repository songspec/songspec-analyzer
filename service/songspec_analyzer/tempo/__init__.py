"""Tempo stage: BPM Fixer's engine (engine.py) -> the contract's `tempo` section with house-style display strings."""
from ..display.bpm import COMPOUND, bpm_display, fmt_bpm, house_bpm  # noqa: F401 (house_bpm re-exported)


def section_from_engine(r):
    """Engine result -> contract `tempo` (Hooktheory may later override `bpm` / `bpm_source`, see links/)."""
    sig, raw = r["signature"], r["bpm_raw"]
    disp = bpm_display(raw, sig, r["sections"])
    bpm = disp["bpm"]
    if sig in COMPOUND:                                   # the tracker's "may work in 4/4 at ...": 2 x the dotted-quarter feel
        alts = [house_bpm(raw * 2.0)[0], round(raw * 1.5, 2)]
        alt_text = [f"{fmt_bpm(alts[0])} (in 4/4)", f"{fmt_bpm(alts[1])} (quarter notes)"]
    else:
        alts = [round(bpm / 2, 2), round(bpm * 2, 2)]
        alt_text = [fmt_bpm(alts[0]), fmt_bpm(alts[1])]
    return {
        "bpm": bpm, "bpm_raw": raw, "bpm_source": "audio",
        "alternates": alts, "signature": sig, "signature_conf": r["signature_conf"], "compound": disp["compound"],
        "sections": r["sections"], "range": disp["range"], "irregularity_pct": r["irregularity_pct"],
        "drift_pct": r["drift_pct"], "feel": r["feel"],
        "beats": r["beats"], "downbeats": r["downbeats"],
        "curve": r["curve"],
        "display": {"bpm": disp["display"], "note": disp["note"], "alternates": alt_text},
    }


def stage(ctx):
    from .engine import analyze
    sig = ctx.options.get("signature") or "Auto"
    r = analyze(ctx.audio.mono, ctx.audio.sr, ctx.settings.models_dir, signature=sig)
    ctx.data["tempo"] = r                                 # beats / downbeats for the chord and key stages
    return {"tempo": section_from_engine(r)}


__all__ = ["stage", "section_from_engine", "house_bpm"]
