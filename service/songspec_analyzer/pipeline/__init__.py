"""The staged analysis pipeline. Each stage owns one section of the result and publishes it as soon as it is done, so the
site can show detune and a quick key within seconds while chords and the fused key are still running.

Stage states (result["stages"][name]): pending | running | done | error | skipped | unavailable
  unavailable = not built into this engine version yet (or its model is missing); the section is simply absent.
"""
import os
import time
import traceback
from dataclasses import dataclass, field
from typing import Callable, Optional

from .. import ENGINE_VERSION, SCHEMA_VERSION
from ..audio.decode import Audio, AudioError, load_audio
from ..audio.fingerprint import chromaprint
from ..config import Settings
from ..store import Store


@dataclass
class Context:
    aid: str
    store: Store
    settings: Settings
    options: dict
    audio: Optional[Audio] = None
    data: dict = field(default_factory=dict)      # intermediate products shared between stages (beats, stems, chroma ...)
    warnings: list = field(default_factory=list)

    def progress(self, stage, message, fraction=None):
        self.store.merge_result(self.aid, {"progress": {"stage": stage, "message": message, "fraction": fraction}})


@dataclass
class Stage:
    name: str
    message: str                                   # shown while it runs ("Finding beats...")
    run: Optional[Callable] = None                 # fn(ctx) -> dict to merge into the result; None = unavailable
    essential: bool = False                        # an error here fails the whole analysis


def _decode(ctx: Context):
    row = ctx.store.get(ctx.aid)
    path = row["audio_path"]
    ctx.audio = load_audio(path)
    if ctx.audio.duration > ctx.settings.max_duration_s + 1:
        raise AudioError(f"This file is {ctx.audio.duration / 60:.1f} minutes long; the limit is "
                         f"{ctx.settings.max_duration_s / 60:.0f} minutes.")
    if ctx.audio.duration < 5.0:
        raise AudioError("This file is shorter than 5 seconds - too short to analyse.")
    fp, _ = chromaprint(path)
    ctx.store.update(ctx.aid, fingerprint=fp, duration=round(ctx.audio.duration, 3))
    track = dict(row["result"].get("track") or {})
    track.update(duration=round(ctx.audio.duration, 3), sample_rate=ctx.audio.sr, channels=int(ctx.audio.samples.shape[1]),
                 fingerprint=fp)
    return {"track": track}


def build_stages():
    """The stage list in run order. Stage modules register themselves here as milestones land."""
    stages = [Stage("decode", "Reading the file...", _decode, essential=True)]
    try:
        from ..detune import stage as detune_stage
        stages.append(Stage("detune", "Measuring detune...", detune_stage))
    except ImportError:
        stages.append(Stage("detune", "Measuring detune..."))
    stages.append(Stage("key_quick", "Estimating the key..."))
    try:
        from ..tempo import stage as tempo_stage
        stages.append(Stage("tempo", "Finding beats...", tempo_stage))
    except ImportError:
        stages.append(Stage("tempo", "Finding beats..."))
    stages += [Stage("stems", "Separating stems..."),
               Stage("chords", "Detecting chords..."),
               Stage("key", "Weighing the key evidence..."),
               Stage("metadata", "Checking Hooktheory...")]
    return stages


def _finish_audio(ctx: Context):
    """Uploaded audio is deleted as soon as the analysis ends (success or not); only derived data is kept."""
    row = ctx.store.get(ctx.aid)
    p = row and row.get("audio_path")
    if p:
        try:
            os.remove(p)
        except OSError:
            pass
        ctx.store.update(ctx.aid, audio_path=None)


def _adopt_cached(ctx: Context) -> bool:
    """Same audio already analysed (fingerprint match found after decoding): copy that result and stop."""
    row = ctx.store.get(ctx.aid)
    hit = ctx.store.find_cached(fingerprint=row["fingerprint"], duration=row["duration"], exclude=ctx.aid)
    if not hit:
        return False
    res = dict(hit["result"])
    res["track"] = {**res.get("track", {}), **row["result"].get("track", {})}
    res["cached"] = True
    ctx.store.update(ctx.aid, result=res, stages=hit["stages"], status="done", cached_from=hit["id"], isrc=hit["isrc"])
    return True


def run_job(aid: str, store: Store, settings: Settings, stages=None):
    """Run every stage of one analysis. Never raises: failures end up in the stored status / stage states."""
    row = store.get(aid)
    if row is None:
        return
    ctx = Context(aid=aid, store=store, settings=settings, options=row["options"])
    stages = stages or build_stages()
    store.update(aid, status="running", stages={s.name: ("pending" if s.run else "unavailable") for s in stages})
    store.merge_result(aid, {"schema_version": SCHEMA_VERSION,
                             "versions": {"engine": ENGINE_VERSION, "models": {}}})
    t0 = time.time()
    try:
        for s in stages:
            if s.run is None:
                continue
            store.set_stage(aid, s.name, "running")
            ctx.progress(s.name, s.message)
            try:
                part = s.run(ctx) or {}
                store.merge_result(aid, part, stage=s.name, stage_state="done")
            except Exception as e:
                if s.essential or isinstance(e, AudioError):
                    raise
                store.set_stage(aid, s.name, "error")
                ctx.warnings.append(f"{s.name} failed: {type(e).__name__}: {str(e)[:200]}")
                traceback.print_exc()
            if s.name == "decode" and _adopt_cached(ctx):
                return
        store.merge_result(aid, {"warnings": ctx.warnings, "progress": None,
                                 "timing": {"seconds": round(time.time() - t0, 2)}})
        store.update(aid, status="done")
    except AudioError as e:
        store.update(aid, status="error", error=str(e))
    except Exception as e:
        traceback.print_exc()
        store.update(aid, status="error", error=f"Internal error ({type(e).__name__}). The analysis could not finish.")
    finally:
        _finish_audio(ctx)
