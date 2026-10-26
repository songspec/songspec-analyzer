// @songspec/analyzer-client: talk to the SongSpec Analyzer service from any frontend (or Node 18+).
// No dependencies, no DOM, no styling: the site owns the UI. See client/README.md and INTEGRATION.md.
//
//   import { createClient } from "./analyzer-client.js";
//   const analyzer = createClient({ baseUrl: "https://analyzer.songspec.org" });
//   const result = await analyzer.analyzeFile(file, { onUpdate: (a) => render(a) });

export class AnalyzerError extends Error {
  constructor(status, code, message, retryAfter = null) {
    super(message);
    this.name = "AnalyzerError";
    this.status = status;
    this.code = code;
    this.retryAfter = retryAfter;
  }
}

const TERMINAL = new Set(["done", "error"]);

export function createClient({ baseUrl = "", apiKey = null, headers = {}, fetch: fetchImpl = globalThis.fetch,
  pollInterval = 1000 } = {}) {
  const base = baseUrl.replace(/\/+$/, "");
  const hdrs = () => ({ ...(apiKey ? { "X-API-Key": apiKey } : {}), ...headers });

  async function request(path, init = {}) {
    const res = await fetchImpl(base + path, { ...init, headers: { ...hdrs(), ...(init.headers || {}) } });
    if (!res.ok) throw await toError(res);
    return res;
  }

  async function toError(res) {
    let code = "http_" + res.status, message = res.statusText || "request failed";
    try {
      const b = await res.json();
      if (b && b.error) ({ code, message } = b.error);
    } catch { /* not JSON */ }
    const ra = res.headers.get("Retry-After");
    return new AnalyzerError(res.status, code, message, ra ? Number(ra) : null);
  }

  /** Start an analysis of a File/Blob. Resolves with the first view ({id, status, cached, ...}). */
  async function start(file, { options = {}, fileName = null, onUploadProgress = null, signal } = {}) {
    const form = new FormData();
    form.append("file", file, fileName || file.name || "upload");
    form.append("options", JSON.stringify(options));
    if (onUploadProgress && typeof XMLHttpRequest !== "undefined") return xhrUpload(form, onUploadProgress, signal);
    const res = await request("/v1/analyses", { method: "POST", body: form, signal });
    return res.json();
  }

  function xhrUpload(form, onUploadProgress, signal) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", base + "/v1/analyses");
      for (const [k, v] of Object.entries(hdrs())) xhr.setRequestHeader(k, v);
      xhr.upload.onprogress = (e) => e.lengthComputable && onUploadProgress(e.loaded / e.total);
      xhr.onload = () => {
        let body = null;
        try { body = JSON.parse(xhr.responseText); } catch { /* ignore */ }
        if (xhr.status >= 200 && xhr.status < 300) return resolve(body);
        const e = body && body.error ? body.error : { code: "http_" + xhr.status, message: xhr.statusText };
        const ra = xhr.getResponseHeader("Retry-After");
        reject(new AnalyzerError(xhr.status, e.code, e.message, ra ? Number(ra) : null));
      };
      xhr.onerror = () => reject(new AnalyzerError(0, "network", "network error"));
      if (signal) signal.addEventListener("abort", () => { xhr.abort(); reject(new DOMException("aborted", "AbortError")); });
      xhr.send(form);
    });
  }

  /** Start an analysis of a Spotify / Apple Music link (30 s official preview + metadata). */
  async function startLink(url, { options = {}, signal } = {}) {
    const res = await request("/v1/analyses", {
      method: "POST", body: JSON.stringify({ url, options }), signal,
      headers: { "Content-Type": "application/json" },
    });
    return res.json();
  }

  const get = async (id, { signal } = {}) => (await request(`/v1/analyses/${encodeURIComponent(id)}`, { signal })).json();

  /** Follow an analysis until it ends. onUpdate(view) fires on every change. Uses the SSE stream (read via fetch so that
   *  API-key headers work) and falls back to polling. Resolves with the final view; rejects with AnalyzerError on status=error. */
  async function watch(id, { onUpdate = () => {}, signal } = {}) {
    let last = null;
    const emit = (v) => {
      if (!last || v.updated_at !== last.updated_at || v.status !== last.status) { last = v; onUpdate(v); }
      return v;
    };
    const finish = (v) => {
      if (v.status === "error") throw new AnalyzerError(422, "analysis_failed", v.error || "the analysis failed");
      return v;
    };
    try {
      const res = await request(`/v1/analyses/${encodeURIComponent(id)}/events`, { signal, headers: { Accept: "text/event-stream" } });
      if (res.body && res.body.getReader) {
        for await (const ev of readSSE(res.body, signal)) {
          const v = emit(JSON.parse(ev.data));
          if (TERMINAL.has(v.status)) return finish(v);
        }
      }
    } catch (e) {
      if (e && e.name === "AbortError") throw e;
      if (e instanceof AnalyzerError && e.status === 404) throw e;
      // stream not available (proxy buffering, old browser): poll instead
    }
    for (;;) {
      const v = emit(await get(id, { signal }));
      if (TERMINAL.has(v.status)) return finish(v);
      await sleep(pollInterval, signal);
    }
  }

  /** Upload + follow in one call. */
  async function analyzeFile(file, opts = {}) {
    const first = await start(file, opts);
    opts.onUpdate?.(first);
    return watch(first.id, opts);           // a cached result is already done: watch returns it after one read
  }

  async function analyzeLink(url, opts = {}) {
    const first = await startLink(url, opts);
    opts.onUpdate?.(first);
    return watch(first.id, opts);
  }

  /** Several files one after another (like BPM Fixer's queue). onFileUpdate(index, view). Errors are collected, not thrown. */
  async function analyzeQueue(files, { onFileUpdate = () => {}, ...opts } = {}) {
    const out = [];
    for (let i = 0; i < files.length; i++) {
      try {
        out.push(await analyzeFile(files[i], { ...opts, onUpdate: (v) => onFileUpdate(i, v) }));
      } catch (e) {
        if (e && e.name === "AbortError") throw e;
        out.push({ status: "error", error: e.message, code: e.code });
      }
    }
    return out;
  }

  /** URL of a server-side export: json | csv | tracker-row | midi | chordpro | txt */
  const exportUrl = (id, format, { transpose = 0, simplify = false } = {}) =>
    `${base}/v1/analyses/${encodeURIComponent(id)}/exports/${format}?transpose=${transpose}&simplify=${simplify ? 1 : 0}`;

  async function exportBlob(id, format, opts) {
    const res = await fetchImpl(exportUrl(id, format, opts), { headers: hdrs() });
    if (!res.ok) throw await toError(res);
    return res.blob();
  }

  /** "This is wrong": field in key | bpm | detune | signature | chords | other */
  async function feedback(id, field, correct, comment = null) {
    const r = await request(`/v1/analyses/${encodeURIComponent(id)}/feedback`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ field, correct, comment }),
    });
    return r.json();
  }

  /** "The key is wrong, it's actually ___": key = any key name ('Bb Minor', 'A# Phrygian', 'F#m'; keys() lists them).
   *  Resolves to the analysis with the corrected key (key.corrected holds what was detected). Every later view of the same song
   *  shows the correction, and the server keeps it for retraining the key model. Rejects with code 'bad_key' for a non-key. */
  async function correctKey(id, key, comment = null) {
    return (await feedback(id, "key", key, comment)).analysis;
  }

  /** Every key a correction can name, house style: [{value: 'A Dorian', key: 'A Minor*', tonic, mode, minor}]. */
  const keys = async () => (await (await request("/v1/keys")).json()).keys;

  const version = async () => (await request("/v1/version")).json();

  /** Fix BPM: BPM Fixer's engine makes the song run at one tempo. Resolves when the file is ready: {id, fix, download}.
   *  options: {target_bpm, signature, bpm_mode: "quarter"|"felt", format: "mp3"|"wav"|"flac"|..., keep_edges, loudness}.
   *  The fixed file: fetch(base + result.download) (or exportBlob-style), kept 24 h for the requester only. */
  async function fixBpm(file, { options = {}, onUpdate = () => {}, signal, fileName = null } = {}) {
    const form = new FormData();
    form.append("file", file, fileName || file.name || "upload");
    form.append("options", JSON.stringify(options));
    let v = await (await request("/v1/fix-bpm", { method: "POST", body: form, signal })).json();
    onUpdate(v);
    while (!TERMINAL.has(v.status)) {
      await sleep(pollInterval, signal);
      v = await (await request(`/v1/fix-bpm/${encodeURIComponent(v.id)}`, { signal })).json();
      onUpdate(v);
    }
    if (v.status === "error") throw new AnalyzerError(422, "fix_failed", v.error || "Fix BPM failed");
    return { ...v, downloadUrl: base + v.download };
  }

  /** Mashup helper: how to make analysis b sit on analysis a (semitones, cents, tempo ratio, Camelot). Both must be done. */
  const match = async (a, b) =>
    (await request(`/v1/match?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`)).json();

  return { start, startLink, get, watch, analyzeFile, analyzeLink, analyzeQueue, exportUrl, exportBlob, feedback, correctKey, keys, version,
    match, fixBpm };
}

// ------------------------------------------------------------------------------------------------ helpers
async function* readSSE(body, signal) {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  try {
    for (;;) {
      if (signal?.aborted) throw new DOMException("aborted", "AbortError");
      const { value, done } = await reader.read();
      if (done) return;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.search(/\r?\n\r?\n/)) >= 0) {
        const raw = buf.slice(0, i);
        buf = buf.slice(i).replace(/^\r?\n\r?\n/, "");
        let event = "message", data = "";
        for (const line of raw.split(/\r?\n/)) {
          if (line.startsWith("event:")) event = line.slice(6).trim();
          else if (line.startsWith("data:")) data += line.slice(5).trimStart();
        }
        if (data) yield { event, data };
      }
    }
  } finally {
    reader.releaseLock?.();
  }
}

function sleep(ms, signal) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener("abort", () => { clearTimeout(t); reject(new DOMException("aborted", "AbortError")); });
  });
}

export * from "./formatters.js";
export { chordNotes, chordpro, midi, progressionText, transform as transformChord, txt } from "./exports.js";
