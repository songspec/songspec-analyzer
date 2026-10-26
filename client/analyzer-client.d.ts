// Types for @songspec/analyzer-client. The full field documentation is contract/analysis.schema.json.

export type StageState = "pending" | "running" | "done" | "error" | "skipped" | "unavailable";
export type StageName = "decode" | "detune" | "key_quick" | "tempo" | "stems" | "chords" | "key" | "metadata" | string;

export interface Track {
  title?: string | null; artist?: string | null; isrc?: string | null; duration?: number | null;
  source: "upload" | "spotify-preview" | "apple-preview" | "deezer-preview";
  file_name?: string | null; sample_rate?: number | null; channels?: number | null; fingerprint?: string | null;
}

export interface Detune {
  cents: number; ci_cents?: number | null; confidence: number; semitones: number; total_cents?: number | null; a4_hz: number;
  drift: { t: number; cents: number; ci?: number | null }[]; drift_flag: boolean;
  secondary?: { cents: number; share: number } | null;
  estimators: Record<string, number | null>;
  display?: { display: string; fix: string; a4_hz: number; semitones: number; total_text?: string | null; key_text?: string | null };
}

export interface Tempo {
  bpm?: number | null; bpm_raw?: number | null; bpm_source: "audio" | "hooktheory"; alternates: number[];
  signature?: string | null; signature_conf?: number | null; compound: boolean;
  sections: TempoSection[]; range?: [number, number] | null; drift_pct?: number | null;
  irregularity_pct?: number | null; feel?: "grid" | "live" | null;
  beats: number[]; downbeats: number[]; curve: { t: number[]; bpm: number[] };
  display?: { bpm?: string | null; note?: string | null; alternates: string[] };
}

export interface Key {
  tonic?: string | null; scale?: "major" | "minor" | null; mode?: string | null; confidence?: number | null; ambiguous: boolean;
  camelot?: string | null; open_key?: string | null;
  candidates: { key: string; p: number }[];
  sections: { start: number; end: number; key: string; display?: string | null }[];
  modulations: { t: number; from: string; to: string; kind?: string }[];
  estimators: Record<string, { key?: string; p?: number; [k: string]: unknown }>;
  metadata: { hooktheory?: { key?: string; bpm?: number; url?: string; agrees?: boolean } ; [k: string]: unknown };
  display?: { key?: string | null; note?: string | null; ambiguity_text?: string | null };
}

export interface ChordSegment {
  start: number; end: number; chord: string; triad?: string | null; roman?: string | null; bass?: string | null;
  conf?: number | null; on_downbeat?: boolean | null;
}

export interface Chords {
  segments: ChordSegment[];
  summary: { chord: string; seconds: number; count: number }[];
  progressions: { roman: string; chords?: string | null; count: number; display?: string | null }[];
  vocabulary: string[];
}

export interface Analysis {
  schema_version: string; id: string; status: "queued" | "running" | "done" | "error"; cached: boolean; error?: string | null;
  stages: Record<StageName, StageState>;
  progress?: { stage: StageName; message: string; fraction?: number | null } | null;
  track?: Track | null; detune?: Detune | null; tempo?: Tempo | null; key?: Key | null; chords?: Chords | null;
  warnings: string[]; versions: { engine?: string; models?: Record<string, string>; [k: string]: unknown };
  created_at?: number; updated_at?: number;
}

export interface AnalyzeOptions { high_accuracy?: boolean; signature?: string; hooktheory?: boolean }

export interface WatchOptions { onUpdate?: (a: Analysis) => void; signal?: AbortSignal }
export interface StartOptions { options?: AnalyzeOptions; fileName?: string; onUploadProgress?: (fraction: number) => void; signal?: AbortSignal }

export type ExportFormat = "json" | "csv" | "tracker-row" | "midi" | "chordpro" | "txt";

export declare class AnalyzerError extends Error {
  status: number; code: string; retryAfter: number | null;
}

export interface AnalyzerClient {
  start(file: Blob, opts?: StartOptions): Promise<Analysis>;
  startLink(url: string, opts?: { options?: AnalyzeOptions; signal?: AbortSignal }): Promise<Analysis>;
  get(id: string, opts?: { signal?: AbortSignal }): Promise<Analysis>;
  watch(id: string, opts?: WatchOptions): Promise<Analysis>;
  analyzeFile(file: Blob, opts?: StartOptions & WatchOptions): Promise<Analysis>;
  analyzeLink(url: string, opts?: { options?: AnalyzeOptions } & WatchOptions): Promise<Analysis>;
  analyzeQueue(files: Blob[], opts?: StartOptions & { onFileUpdate?: (index: number, a: Analysis) => void; signal?: AbortSignal }):
    Promise<(Analysis | { status: "error"; error: string; code?: string })[]>;
  exportUrl(id: string, format: ExportFormat, opts?: { transpose?: number; simplify?: boolean }): string;
  exportBlob(id: string, format: ExportFormat, opts?: { transpose?: number; simplify?: boolean }): Promise<Blob>;
  feedback(id: string, field: "key" | "bpm" | "detune" | "signature" | "chords" | "other", correct: string, comment?: string | null): Promise<void>;
  version(): Promise<{ engine: string; schema: string; formats: string[]; limits: { max_upload_mb: number; max_duration_s: number } }>;
  match(a: string, b: string): Promise<MatchAdvice>;
  fixBpm(file: Blob, opts?: { options?: FixOptions; onUpdate?: (v: FixJob) => void; signal?: AbortSignal; fileName?: string }):
    Promise<FixJob & { downloadUrl: string }>;
}

export interface FixOptions { target_bpm?: number; signature?: string; bpm_mode?: "quarter" | "felt";
  format?: "mp3" | "wav" | "wav16" | "flac" | "aiff" | "m4a" | "ogg"; keep_edges?: boolean; loudness?: boolean }
export interface FixJob {
  id: string; status: "queued" | "running" | "done" | "error"; error?: string | null; options: FixOptions;
  progress?: { stage: string; message: string } | null; download?: string;
  fix?: { target_bpm: number; signature: string; file_name: string;
          stretch: { slowest: number; fastest: number; pct_over_20: number; heavy: [number, number, number][] };
          verify: { on_grid_pct: number; median_ms: number; with_hit_pct: number } | null;
          irregularity_before_pct: number; log: string[] };
}

export interface MatchAdvice {
  a: string; b: string;
  key: { semitones: number; a: string; b: string; b_after: string;
         camelot_before: "same" | "relative" | "adjacent" | null; camelot_after: "same" | "relative" | "adjacent" | null } | null;
  tuning: { fine_cents: number; total_cents: number };
  tempo: { ratio: number; stretch_pct: number; b_level: "half time" | "double time" | null; b_bpm_after: number; varispeed_bpm: number } | null;
  display: { key?: string; tuning: string; tempo?: string; varispeed?: string };
}

export declare function createClient(opts?: {
  baseUrl?: string; apiKey?: string | null; headers?: Record<string, string>; fetch?: typeof fetch; pollInterval?: number;
}): AnalyzerClient;

// ---- formatters (house style)
export type Mode = "major" | "minor" | "ionian" | "dorian" | "phrygian" | "lydian" | "mixolydian" | "aeolian" | "locrian"
  | "harmonic_minor" | "phrygian_dominant";
export declare const FLAT_NAMES: string[];
export declare function parsePitch(name: string): number;
export declare function parseKey(text: string): { tonic: number; mode: Mode };
export declare function houseKey(tonic: number, mode: Mode | string): string;
export declare function camelot(tonic: number, mode: Mode | string): string;
export declare function openKey(tonic: number, mode: Mode | string): string;
export declare function keyDisplay(tonic: number, mode: Mode | string, opts?: { source?: "hooktheory" | null; sourceText?: string | null }):
  { key: string; note: string | null; camelot: string; open_key: string };
export declare function sectionsDisplay(sections: { start: number; end: number; tonic: number; mode: Mode | string }[], labels?: string[] | null):
  { key: string | null; note: string | null };
export declare function houseBpm(raw: number, signature?: string): { bpm: number | null; compound: boolean };
export declare function fmtBpm(bpm: number | null): string | null;
export interface TempoSection { start: number; end: number; bpm: number; lo?: number; hi?: number; drift_pct?: number;
  steadiness?: "steady" | "moderate" | "live" }
export declare function sectionText(section: TempoSection, signature?: string): string;
export declare function bpmDisplay(raw: number, signature?: string, sections?: TempoSection[] | null):
  { bpm: number | null; display: string | null; note: string | null; compound: boolean; range: [number, number] | null };
export declare function detuneDisplay(cents: number, opts?: { totalCents?: number | null; tonic?: number | null; mode?: Mode | string | null }):
  { display: string; fix: string; a4_hz: number; semitones: number; total_text: string | null; key_text: string | null };
export declare function parseChord(name: string): { root: number; quality: string; bass: number | null } | null;
export declare function chordName(root: number, quality: string, bass?: number | null): string;
export declare function roman(root: number, quality: string, tonic: number, mode: Mode | string): string;
export declare function transposeChord(name: string, semitones: number): string;
export declare function simplifyChord(name: string): string;
export declare function trackerRow(song: string, bpm: string, key: string, notes: (string | null | undefined)[]): string;
export declare function trackerRowFor(analysis: Analysis): string;

// ---- chord exports in the browser (byte-identical to GET /v1/analyses/{id}/exports/{midi|chordpro|txt})
export interface ExportOptions { transpose?: number; simplify?: boolean }
export declare function midi(analysis: Analysis, opts?: ExportOptions): Uint8Array;
export declare function chordpro(analysis: Analysis, opts?: ExportOptions): string;
export declare function txt(analysis: Analysis, opts?: ExportOptions): string;
export declare function progressionText(analysis: Analysis, opts?: ExportOptions): string;
export declare function transformChord(name: string, transpose?: number, simplify?: boolean): string;
export declare function chordNotes(name: string): number[];
