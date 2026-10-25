"""The result contract (schema 1.x). Pydantic models are the source of truth; `python -m songspec_analyzer.contract` writes
contract/analysis.schema.json, and a test fails if the committed copy is out of date.

Rules for the site:
- Every section is optional while the analysis runs: check `stages[name] == "done"` before relying on it.
- `display` objects hold the house-style strings; show them as they are. The raw numbers next to them are for logic and custom UI.
- Times are seconds from the start of the file. Cents are relative to 12-TET A440.
- New fields may appear in any 1.x version; nothing is removed or renamed before 2.0.
"""
import json
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

from . import SCHEMA_VERSION

StageState = Literal["pending", "running", "done", "error", "skipped", "unavailable"]


class _M(BaseModel):
    model_config = ConfigDict(extra="allow")


class Progress(_M):
    stage: str
    message: str = Field(description="Human-readable, e.g. 'Finding beats...'")
    fraction: Optional[float] = Field(None, description="0..1 within the stage, when known")


class Track(_M):
    title: Optional[str] = None
    artist: Optional[str] = None
    isrc: Optional[str] = None
    duration: Optional[float] = Field(None, description="seconds")
    source: Literal["upload", "spotify-preview", "apple-preview", "deezer-preview"] = "upload"
    file_name: Optional[str] = None
    sample_rate: Optional[int] = None
    channels: Optional[int] = None
    fingerprint: Optional[str] = Field(None, description="Chromaprint (when fpcalc is installed)")


class DriftPoint(_M):
    t: float = Field(description="window centre, seconds")
    cents: float
    ci: Optional[float] = Field(None, description="95 % half-width, cents")


class DetuneDisplay(_M):
    display: str = Field(description="'+23.4 ¢'")
    fix: str = Field(description="'pitch by −23.4 ¢ to fix'")
    a4_hz: float
    semitones: int = 0
    total_text: Optional[str] = Field(None, description="'+1 semitone, −18.0 ¢' when the full transposition is known")
    key_text: Optional[str] = Field(None, description="'Plays in Bb Minor (+38.0 ¢ above Bb). If you pitch down 38.0 ¢, ...'")


class Detune(_M):
    cents: float = Field(description="reference-pitch offset from A440, wrapped to [-50, 50), 0.1 ¢ resolution")
    ci_cents: Optional[float] = Field(None, description="95 % confidence half-width")
    confidence: float = Field(description="0..1")
    semitones: int = Field(0, description="whole semitones of transposition when known (else 0)")
    total_cents: Optional[float] = None
    a4_hz: float
    drift: List[DriftPoint] = Field(default_factory=list, description="tuning per window; meaningful when drift_flag")
    drift_flag: bool = Field(False, description="the tuning moves by more than 5 cents over the song")
    secondary: Optional[dict] = Field(None, description="a second tuning in the mix (e.g. an 808 off the rest): {cents, share}")
    estimators: Dict[str, Optional[float]] = Field(default_factory=dict, description="each estimator's vote, cents")
    display: Optional[DetuneDisplay] = None


class TempoSection(_M):
    start: float
    end: float
    bpm: float = Field(description="raw felt BPM of the section")


class TempoCurve(_M):
    t: List[float] = Field(default_factory=list)
    bpm: List[float] = Field(default_factory=list)


class TempoDisplay(_M):
    bpm: Optional[str] = Field(None, description="'114', '82.5', '~122 - 127'")
    note: Optional[str] = Field(None, description="'BPM inconsistent - played live', 'Played in 6/8 time signature'")
    alternates: List[str] = Field(default_factory=list, description="½x / 1x / 2x as display strings, for chips")


class Tempo(_M):
    bpm: Optional[float] = Field(None, description="house-rule BPM (80-160 unless compound meter), 2 decimals")
    bpm_raw: Optional[float] = Field(None, description="the felt tempo as detected")
    bpm_source: Literal["audio", "hooktheory"] = "audio"
    alternates: List[float] = Field(default_factory=list)
    signature: Optional[str] = None
    signature_conf: Optional[float] = None
    compound: bool = False
    sections: List[TempoSection] = Field(default_factory=list)
    range: Optional[List[float]] = Field(None, description="[lo, hi] house-rule BPM when played live")
    irregularity_pct: Optional[float] = None
    feel: Optional[Literal["grid", "live"]] = None
    beats: List[float] = Field(default_factory=list)
    downbeats: List[float] = Field(default_factory=list)
    curve: TempoCurve = Field(default_factory=TempoCurve, description="per-beat tempo, at most 1500 points")
    display: Optional[TempoDisplay] = None


class KeyCandidate(_M):
    key: str = Field(description="house style, e.g. 'Bb Minor'")
    p: float


class KeySection(_M):
    start: float
    end: float
    key: str = Field(description="full name incl. mode, e.g. 'C Phrygian Dominant'")
    display: Optional[str] = Field(None, description="house style, e.g. 'C Minor*'")


class KeyDisplay(_M):
    key: Optional[str] = Field(None, description="'Bb Minor*', 'C Minor* / C Major'")
    note: Optional[str] = Field(None, description="'A# Phrygian', 'Intro in C Phrygian Dominant, Verses & Choruses in C Major'")
    ambiguity_text: Optional[str] = Field(None, description="'ambiguous between C Major and A Minor' when confidence is low")


class Key(_M):
    tonic: Optional[str] = Field(None, description="flat spelling, 'Bb'")
    scale: Optional[Literal["major", "minor"]] = None
    mode: Optional[str] = Field(None, description="'Phrygian' etc. when it clearly beats plain major/minor; else null")
    confidence: Optional[float] = Field(None, description="calibrated probability of the top key, 0..1")
    ambiguous: bool = False
    camelot: Optional[str] = None
    open_key: Optional[str] = None
    candidates: List[KeyCandidate] = Field(default_factory=list, description="top 3")
    sections: List[KeySection] = Field(default_factory=list)
    modulations: List[dict] = Field(default_factory=list, description="[{t, from, to, kind}] e.g. kind 'half-step lift'")
    estimators: Dict[str, dict] = Field(default_factory=dict, description="each estimator's top key and probability")
    metadata: Dict[str, dict] = Field(default_factory=dict, description="{'hooktheory': {key, bpm, url, agrees}}")
    display: Optional[KeyDisplay] = None


class ChordSegment(_M):
    start: float
    end: float
    chord: str = Field(description="'Bbm', 'Ebmaj7', 'C/E'; 'N' = no chord")
    triad: Optional[str] = Field(None, description="the chord reduced to a triad, for the simplify toggle")
    roman: Optional[str] = None
    bass: Optional[str] = None
    conf: Optional[float] = None
    on_downbeat: Optional[bool] = None


class ChordSummaryItem(_M):
    chord: str
    seconds: float
    count: int = 0


class Progression(_M):
    roman: str = Field(description="'i-VI-III-VII'")
    chords: Optional[str] = Field(None, description="'Bbm-Gb-Db-Ab'")
    count: int
    display: Optional[str] = Field(None, description="'i–VI–III–VII ×8'")


class Chords(_M):
    segments: List[ChordSegment] = Field(default_factory=list)
    summary: List[ChordSummaryItem] = Field(default_factory=list)
    progressions: List[Progression] = Field(default_factory=list)
    vocabulary: List[str] = Field(default_factory=list)


class Analysis(_M):
    """GET /v1/analyses/{id}"""
    schema_version: str = SCHEMA_VERSION
    id: str
    status: Literal["queued", "running", "done", "error"]
    cached: bool = False
    error: Optional[str] = Field(None, description="user-safe message when status = error")
    stages: Dict[str, StageState] = Field(default_factory=dict)
    progress: Optional[Progress] = None
    track: Optional[Track] = None
    detune: Optional[Detune] = None
    tempo: Optional[Tempo] = None
    key: Optional[Key] = None
    chords: Optional[Chords] = None
    warnings: List[str] = Field(default_factory=list)
    versions: Dict[str, object] = Field(default_factory=dict)
    created_at: Optional[float] = None
    updated_at: Optional[float] = None


SCHEMA_PATH = Path(__file__).resolve().parents[2] / "contract" / "analysis.schema.json"


def schema_text():
    s = Analysis.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["$id"] = f"https://songspec.org/schemas/analysis-{SCHEMA_VERSION}.json"
    return json.dumps(s, indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
    SCHEMA_PATH.write_text(schema_text(), encoding="utf-8")
    print(f"wrote {SCHEMA_PATH}")
