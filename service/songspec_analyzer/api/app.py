"""HTTP API (v1). Everything the site needs: start an analysis (file upload or streaming link), follow it (poll or SSE),
read the result, export it, send a correction.

Run:  uvicorn songspec_analyzer.api.app:app --app-dir service
Embed: from songspec_analyzer.api.app import create_app; app = create_app(settings)
"""
import asyncio
import json
import os
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from .. import ENGINE_VERSION, SCHEMA_VERSION
from ..audio.decode import AUDIO_EXTS, probe_duration
from ..audio.fingerprint import file_sha256
from ..config import Settings, get_settings
from ..jobs import AudioSweeper, make_queue
from ..store import Store
from .errors import ApiError
from .limits import RateLimiter

OPTION_KEYS = {"high_accuracy": bool, "signature": str, "hooktheory": bool}
FEEDBACK_FIELDS = ("key", "bpm", "detune", "signature", "chords", "other")


def _err(status, code, message, headers=None, extra=None):
    return JSONResponse({"error": {"code": code, "message": message, **(extra or {})}}, status_code=status, headers=headers)


def public_view(row, store=None):
    """What clients see: status + the result so far. Internal columns (paths, hashes) are never exposed.
    With the store, a user's key correction for this song is applied (key/correct.py)."""
    res = dict(row["result"])
    out = {"id": row["id"], "status": row["status"], "cached": bool(row.get("cached_from")) or bool(res.pop("cached", False)),
           "stages": row["stages"], "error": row.get("error"), "schema_version": res.pop("schema_version", SCHEMA_VERSION)}
    out.update(res)
    out.setdefault("warnings", [])
    out["created_at"], out["updated_at"] = row["created_at"], row["updated_at"]
    if store is not None and out.get("key"):
        from ..key import correct
        correct.apply(out, store.key_correction(row))
    return out


def _parse_options(raw):
    if not raw:
        return {}
    try:
        d = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (ValueError, TypeError):
        raise ApiError(422, "bad_options", "options must be a JSON object")
    out = {}
    for k, typ in OPTION_KEYS.items():
        if k in d and d[k] is not None:
            out[k] = typ(d[k])
    return out


def create_app(settings: Settings = None) -> FastAPI:
    settings = settings or get_settings()
    settings.ensure_dirs()
    state = {}

    @asynccontextmanager
    async def lifespan(app):
        store = Store(settings.db_path)
        queue = make_queue(store, settings)
        sweeper = AudioSweeper(store, settings)
        sweeper.start()
        for aid in store.stale_jobs():                        # restart recovery: re-run jobs whose audio is still here
            row = store.get(aid)
            if row["audio_path"] and os.path.isfile(row["audio_path"]):
                queue.submit(aid, "fix" if row["source"] == "fix-bpm" else "analysis")
            else:
                store.update(aid, status="error", error="The service restarted before this analysis finished. Please try again.")
        state.update(store=store, queue=queue, sweeper=sweeper,
                     limiter=RateLimiter(settings.rate_per_min, settings.rate_burst))
        yield
        sweeper.stop()
        queue.shutdown()

    app = FastAPI(title="SongSpec Analyzer", version=ENGINE_VERSION, lifespan=lifespan,
                  description="Key, BPM, chords and detune for songspec.org. See INTEGRATION.md.")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET", "POST", "OPTIONS"],
                       allow_headers=["*"], expose_headers=["Retry-After"])
    app.state.s = state

    @app.exception_handler(ApiError)
    async def _api_error(request, e: ApiError):
        return _err(e.status, e.code, e.message, e.headers, e.extra)

    def client_key(request: Request):
        if settings.api_keys:
            k = request.headers.get("x-api-key", "")
            if k not in settings.api_keys:
                raise ApiError(401, "unauthorized", "missing or wrong X-API-Key")
        user = request.headers.get("x-songspec-user")
        return ("user:" + user) if user else ("ip:" + (request.client.host if request.client else "?"))

    def check_rate(key):
        ok, wait = state["limiter"].allow(key)
        if not ok:
            raise ApiError(429, "rate_limited", f"Too many analyses - try again in {int(wait) + 1} s.",
                           {"Retry-After": str(int(wait) + 1)})

    def get_row(aid):
        row = state["store"].get(aid)
        if row is None:
            raise ApiError(404, "not_found", "no such analysis")
        return row

    # ------------------------------------------------------------------ start
    @app.post("/v1/analyses", status_code=202)
    async def create_analysis(request: Request, file: UploadFile = File(None), url: str = Form(None),
                              options: str = Form(None)):
        key = client_key(request)
        if file is None and url is None and request.headers.get("content-type", "").startswith("application/json"):
            body = await request.json()
            url, options = body.get("url"), body.get("options")
        opts = {"high_accuracy": settings.high_accuracy_default, **_parse_options(options)}
        if file is None and not url:
            raise ApiError(422, "no_input", "send a file (multipart field 'file') or a link ('url')")
        check_rate(key)
        store = state["store"]
        if url:
            from ..links import resolve_and_fetch   # milestone 7
            return await resolve_and_fetch(url, opts, store, state["queue"], settings)

        name = Path(file.filename or "upload").name
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        dest = settings.uploads_dir / f"{uuid.uuid4().hex}.{ext or 'bin'}"
        limit = int(settings.max_upload_mb * 1024 * 1024)
        size = 0
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise ApiError(413, "too_large", f"Files up to {settings.max_upload_mb:g} MB are accepted.")
                    f.write(chunk)
            if size == 0:
                raise ApiError(422, "empty_file", "the file is empty")
            dur = await asyncio.to_thread(probe_duration, str(dest))
            if dur is not None and dur > settings.max_duration_s + 1:
                raise ApiError(413, "too_long", f"Songs up to {settings.max_duration_s / 60:g} minutes are accepted "
                                                f"(this one is {dur / 60:.1f}).")
            sha = await asyncio.to_thread(file_sha256, str(dest))
        except BaseException:
            dest.unlink(missing_ok=True)
            raise

        hit = store.find_cached(file_sha256=sha)
        aid = store.create("upload", file_name=name, options=opts, audio_path=str(dest))
        store.update(aid, file_sha256=sha, result={"track": {"title": None, "artist": None, "file_name": name,
                                                             "source": "upload"}})
        if hit:                                               # the exact same file was analysed before: answer at once
            dest.unlink(missing_ok=True)
            res = dict(hit["result"])
            res["track"] = {**res.get("track", {}), "file_name": name}
            store.update(aid, status="done", result=res, stages=hit["stages"], cached_from=hit["id"], audio_path=None,
                         fingerprint=hit["fingerprint"], duration=hit["duration"], isrc=hit["isrc"])
        else:
            state["queue"].submit(aid)
        return public_view(store.get(aid), state["store"])

    # ------------------------------------------------------------------ follow
    @app.get("/v1/analyses/{aid}")
    async def get_analysis(aid: str):
        return public_view(get_row(aid), state["store"])

    @app.get("/v1/analyses/{aid}/events")
    async def events(aid: str, request: Request):
        """Server-sent events. Every change sends `event: update` with the full current view; the stream ends with
        `event: done` or `event: error`. Works with any queue backend (it follows the store)."""
        get_row(aid)
        store = state["store"]

        async def gen():
            last = None
            started = time.time()
            yield "retry: 2000\n\n"
            while True:
                if await request.is_disconnected():
                    return
                row = store.get(aid)
                if row["updated_at"] != last:
                    last = row["updated_at"]
                    view = json.dumps(public_view(row, state["store"]), separators=(",", ":"))
                    if row["status"] in ("done", "error"):
                        yield f"event: {row['status']}\ndata: {view}\n\n"
                        return
                    yield f"event: update\ndata: {view}\n\n"
                elif time.time() - started > 3600:
                    return
                else:
                    yield ": keep-alive\n\n" if int(time.time()) % 15 == 0 else ""
                await asyncio.sleep(0.4)

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    # ------------------------------------------------------------------ exports
    @app.get("/v1/analyses/{aid}/exports/{fmt}")
    async def export(aid: str, fmt: str, transpose: int = 0, simplify: bool = False):
        row = get_row(aid)
        if row["status"] != "done":
            raise ApiError(409, "not_ready", "the analysis has not finished yet")
        view = public_view(row, state["store"])
        from ..export import EXPORTERS
        if fmt not in EXPORTERS:
            raise ApiError(404, "unknown_format", f"formats: {', '.join(EXPORTERS)}")
        body, media, ext = EXPORTERS[fmt](view, transpose=transpose, simplify=simplify)
        base = Path(view.get("track", {}).get("file_name") or "analysis").stem
        headers = {"Content-Disposition": f'attachment; filename="{base}.{ext}"'}
        return Response(body, media_type=media, headers=headers)

    # ------------------------------------------------------------------ feedback
    @app.post("/v1/analyses/{aid}/feedback", status_code=201)
    async def feedback(aid: str, request: Request):
        get_row(aid)
        body = await request.json()
        field, value = body.get("field"), body.get("correct")
        if field not in FEEDBACK_FIELDS or not isinstance(value, (str, int, float)) or not str(value).strip():
            raise ApiError(422, "bad_feedback", f"send {{field: one of {list(FEEDBACK_FIELDS)}, correct: '...'}}")
        value = str(value).strip()[:200]
        if field == "key":                                    # must be a real key: it is shown in place of the detected one
            from ..key import correct
            try:
                correct.normalise(value)
            except ValueError:
                raise ApiError(422, "bad_key", "correct must be a key such as 'Bb Minor', 'A# Phrygian', 'F#m' "
                                               "(GET /v1/keys lists every choice)")
        state["store"].add_feedback(aid, field, value, (body.get("comment") or "")[:1000] or None,
                                    request.headers.get("x-songspec-user"))
        return {"ok": True, "analysis": public_view(get_row(aid), state["store"]) if field == "key" else None}

    @app.get("/v1/keys")
    async def keys():
        """Every key a correction can name (house style), for a picker: 24 major/minor keys and each mode on each tonic."""
        from ..key import correct
        return {"keys": correct.choices()}

    # ------------------------------------------------------------------ Fix BPM (BPM Fixer's process() on the server)
    @app.post("/v1/fix-bpm", status_code=202)
    async def fix_bpm(request: Request, file: UploadFile = File(...), options: str = Form(None)):
        """Make a song run at one constant BPM: BPM Fixer's own engine (Rubber Band R3). Options (JSON): target_bpm (default: the
        song's median), signature ('Auto', '4/4', '6/8' ...), bpm_mode ('quarter' | 'felt'), format ('mp3', 'wav', 'flac', ...),
        keep_edges (leave intro / outro untouched), loudness (keep the original loudness)."""
        from ..tempo.fixbpm import FORMATS, rubberband_path
        from ..tempo._bpmfix_fix import HAVE_PYLIBRB
        key = client_key(request)
        if not rubberband_path() and not HAVE_PYLIBRB:
            raise ApiError(501, "fix_unavailable", "Fix BPM is not available on this server (Rubber Band is not installed).")
        try:
            o = json.loads(options) if options else {}
        except ValueError:
            raise ApiError(422, "bad_options", "options must be a JSON object")
        opts = {"target_bpm": float(o["target_bpm"]) if o.get("target_bpm") else None,
                "signature": str(o.get("signature", "Auto")), "bpm_mode": "felt" if o.get("bpm_mode") == "felt" else "quarter",
                "format": o.get("format", "mp3") if o.get("format", "mp3") in FORMATS else "mp3",
                "keep_edges": bool(o.get("keep_edges", True)), "loudness": bool(o.get("loudness", True))}
        check_rate(key)
        name = Path(file.filename or "upload").name
        dest = settings.uploads_dir / f"{uuid.uuid4().hex}_{name}"
        limit, size = int(settings.max_upload_mb * 1024 * 1024), 0
        try:
            with open(dest, "wb") as f:
                while True:
                    chunk = await file.read(1 << 20)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise ApiError(413, "too_large", f"Files up to {settings.max_upload_mb:g} MB are accepted.")
                    f.write(chunk)
            dur = await asyncio.to_thread(probe_duration, str(dest))
            if dur is not None and dur > settings.max_duration_s + 1:
                raise ApiError(413, "too_long", f"Songs up to {settings.max_duration_s / 60:g} minutes are accepted.")
        except BaseException:
            dest.unlink(missing_ok=True)
            raise
        store = state["store"]
        aid = store.create("fix-bpm", file_name=name, options=opts, audio_path=str(dest))
        state["queue"].submit(aid, "fix")
        return fix_view(store.get(aid))

    def fix_view(row):
        res = row["result"]
        out = {"id": row["id"], "status": row["status"], "error": row.get("error"), "options": row["options"],
               "progress": res.get("progress"), "fix": res.get("fix")}
        if row["status"] == "done" and row.get("output_path"):
            out["download"] = f"/v1/fix-bpm/{row['id']}/download"
        return out

    def get_fix(aid):
        row = state["store"].get(aid)
        if row is None or row["source"] != "fix-bpm":
            raise ApiError(404, "not_found", "no such Fix BPM job")
        return row

    @app.get("/v1/fix-bpm/{aid}")
    async def fix_status(aid: str):
        return fix_view(get_fix(aid))

    @app.get("/v1/fix-bpm/{aid}/download")
    async def fix_download(aid: str):
        from fastapi.responses import FileResponse
        row = get_fix(aid)
        p = row.get("output_path")
        if row["status"] != "done" or not p or not os.path.isfile(p):
            raise ApiError(409 if row["status"] != "done" else 410, "not_ready" if row["status"] != "done" else "expired",
                           "not finished yet" if row["status"] != "done" else "the fixed file has expired (24 h)")
        return FileResponse(p, filename=os.path.basename(p).split("_", 1)[-1])     # without the upload's id prefix

    # ------------------------------------------------------------------ mashup helper
    @app.get("/v1/match")
    async def match(a: str, b: str):
        """How to make song b sit on song a: semitones, cents, tempo ratio, Camelot compatibility."""
        from ..match import advise
        ra, rb = get_row(a), get_row(b)
        if ra["status"] != "done" or rb["status"] != "done":
            raise ApiError(409, "not_ready", "both analyses must have finished")
        return advise(public_view(ra, state["store"]), public_view(rb, state["store"]))

    # ------------------------------------------------------------------ ops
    @app.get("/v1/health")
    async def health():
        return {"ok": True}

    @app.get("/v1/version")
    async def version():
        return {"engine": ENGINE_VERSION, "schema": SCHEMA_VERSION, "formats": AUDIO_EXTS,
                "limits": {"max_upload_mb": settings.max_upload_mb, "max_duration_s": settings.max_duration_s}}

    @app.get("/v1/schema", response_class=PlainTextResponse)
    async def schema():
        p = Path(__file__).resolve().parents[3] / "contract" / "analysis.schema.json"
        return PlainTextResponse(p.read_text(encoding="utf-8") if p.exists() else "{}", media_type="application/json")

    if settings.serve_demo:                                   # development only: the reference demo page and the client tests
        from fastapi.responses import RedirectResponse
        from fastapi.staticfiles import StaticFiles
        root = Path(__file__).resolve().parents[3]
        app.mount("/demo", StaticFiles(directory=root / "demo", html=True), name="demo")
        app.mount("/client", StaticFiles(directory=root / "client"), name="client")
        if (root / "service" / "tests").is_dir():            # the client tests' shared cases (not in the tester zip)
            app.mount("/cases", StaticFiles(directory=root / "service" / "tests"), name="cases")
        app.add_api_route("/", lambda: RedirectResponse("/demo/"), include_in_schema=False)

    return app


app = create_app()
