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
    await request(`/v1/analyses/${encodeURIComponent(id)}/feedback`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ field, correct, comment }),
    });
  }

  const version = async () => (await request("/v1/version")).json();

  return { start, startLink, get, watch, analyzeFile, analyzeLink, analyzeQueue, exportUrl, exportBlob, feedback, version };
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
