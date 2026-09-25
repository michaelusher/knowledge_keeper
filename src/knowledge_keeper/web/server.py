"""Local website for knowledge-keeper: a JSON API under /api plus the single-page UI.

Security model (it's a local app, but a web server is a web server):
- By default it binds to 127.0.0.1, so only this computer can reach it.
- With sharing on, other devices must present an access code (cookie or ?code=).
- Machine-level actions (open folder, create shortcut, shut down) are
  localhost-only even when sharing.
- Every state-changing request must carry the X-KK-Client header, which a web
  page on another site can't send without a CORS preflight this server never
  approves — so a random website can't drive your knowledge base.
- Host headers are checked against localhost names to block DNS rebinding
  (when not sharing).
"""
from __future__ import annotations

import hmac
import os
import signal
import threading
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..service import BusyError, KnowledgeKeeper, ServiceError

STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
LOCAL_HOSTNAMES = {"localhost", "127.0.0.1", "[::1]", "::1"}
CLIENT_HEADER = "x-kk-client"
COOKIE = "kk_access"


class AskBody(BaseModel):
    question: str
    doc_ids: Optional[list[str]] = None
    top_k: int = 8


class FolderBody(BaseModel):
    path: str


class RunBody(BaseModel):
    use_llm: bool = True


class SettingsBody(BaseModel):
    provider: Optional[str] = None
    values: Optional[dict] = None


class KeyBody(BaseModel):
    name: str
    value: str
    remember: bool = True


class OpenBody(BaseModel):
    which: str


def _is_loopback(request: Request) -> bool:
    host = request.client.host if request.client else ""
    return host in LOOPBACK


def create_app(service: KnowledgeKeeper, access_code: Optional[str] = None, on_shutdown=None) -> FastAPI:
    app = FastAPI(title="Knowledge Keeper", version=service.status()["version"], docs_url="/api/docs",
                  redoc_url=None, openapi_url="/api/openapi.json")
    sharing = access_code is not None

    # ------------------------------------------------------ guard middleware
    @app.middleware("http")
    async def guard(request: Request, call_next):
        local = _is_loopback(request)
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].lower()
        if not sharing and host and host not in LOCAL_HOSTNAMES:
            return PlainTextResponse("Unexpected Host header.", status_code=403)

        if sharing and not local:
            code = request.query_params.get("code")
            if code and hmac.compare_digest(code, access_code):
                resp = RedirectResponse(url=request.url.path or "/", status_code=303)
                resp.set_cookie(COOKIE, access_code, httponly=True, samesite="strict")
                return resp
            cookie = request.cookies.get(COOKIE, "")
            if not (cookie and hmac.compare_digest(cookie, access_code)):
                if request.url.path.startswith("/api/"):
                    return JSONResponse({"detail": "Access code required."}, status_code=401)
                return Response(_LOCKED_PAGE, media_type="text/html", status_code=401)

        if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith("/api/"):
            if request.headers.get(CLIENT_HEADER) != "1":
                return JSONResponse({"detail": "Missing client header."}, status_code=403)
        return await call_next(request)

    def local_only(request: Request) -> None:
        if not _is_loopback(request):
            raise HTTPException(403, "Only available on the computer running Knowledge Keeper.")

    def run(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except BusyError as exc:
            raise HTTPException(409, str(exc)) from exc
        except ServiceError as exc:
            raise HTTPException(400, str(exc)) from exc

    # ------------------------------------------------------------- status
    @app.get("/api/health")
    def health():
        return {"app": "knowledge-keeper", "workspace": str(service.workspace)}

    @app.get("/api/status")
    def status(request: Request):
        data = service.status()
        data["is_local_client"] = _is_loopback(request)
        return data

    # ---------------------------------------------------------- documents
    @app.get("/api/documents")
    def documents():
        return service.list_documents()

    @app.post("/api/documents/upload")
    async def upload(files: list[UploadFile] = File(...)):
        saved, rejected = [], {}
        for f in files:
            data = await f.read()
            try:
                saved.append(service.save_upload(f.filename or "upload", data))
            except ServiceError as exc:
                rejected[f.filename or "upload"] = str(exc)
        if not saved:
            raise HTTPException(400, "; ".join(rejected.values()) or "No files received.")
        label = saved[0].name if len(saved) == 1 else f"Add {len(saved)} files"
        job = run(service.start_ingest, saved, label)
        return {"job": job.to_dict(), "rejected": rejected}

    @app.post("/api/documents/import-folder")
    def import_folder(body: FolderBody, request: Request):
        local_only(request)
        return {"job": run(service.start_import_folder, body.path).to_dict()}

    @app.post("/api/documents/rescan")
    def rescan():
        return {"job": run(service.start_rescan).to_dict()}

    @app.post("/api/documents/demo")
    def demo():
        return {"job": run(service.start_demo).to_dict()}

    @app.delete("/api/documents/{doc_id}")
    def remove(doc_id: str, delete_file: bool = True):
        return run(service.remove_document, doc_id, delete_file)

    # ---------------------------------------------------------------- ask
    @app.post("/api/ask")
    def ask(body: AskBody):
        return run(service.ask, body.question, body.doc_ids, max(1, min(body.top_k, 25)))

    # ----------------------------------------------------- analysis/report
    @app.post("/api/analyze")
    def analyze(body: RunBody):
        return {"job": run(service.start_analyze, body.use_llm).to_dict()}

    @app.get("/api/analysis")
    def analysis():
        return service.get_analysis()  # null until the first analysis runs

    @app.post("/api/report")
    def report(body: RunBody):
        return {"job": run(service.start_report, body.use_llm).to_dict()}

    @app.get("/api/report/latest")
    def latest_report():
        return service.latest_report()  # null until the first report is generated

    @app.get("/api/report/download")
    def download_report():
        data = service.latest_report()
        if data is None:
            raise HTTPException(404, "No report yet.")
        return FileResponse(data["path"], media_type="text/markdown", filename=data["name"])

    # --------------------------------------------------------------- jobs
    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        j = service.jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Unknown job.")
        return j.to_dict()

    # ----------------------------------------------------------- settings
    @app.get("/api/settings")
    def get_settings():
        return service.get_settings()

    @app.put("/api/settings")
    def put_settings(body: SettingsBody, request: Request):
        local_only(request)
        return run(service.update_settings, body.provider, body.values)

    @app.post("/api/settings/key")
    def save_key(body: KeyBody, request: Request):
        local_only(request)
        return run(service.save_key, body.name, body.value, body.remember)

    @app.delete("/api/settings/key/{name}")
    def forget_key(name: str, request: Request):
        local_only(request)
        return run(service.forget_key, name)

    @app.post("/api/settings/test-llm")
    def test_llm():
        return service.test_llm()

    # -------------------------------------------------- this-computer only
    @app.post("/api/open-folder")
    def open_folder(body: OpenBody, request: Request):
        local_only(request)
        return run(service.open_folder, body.which)

    @app.post("/api/shortcut")
    def shortcut(request: Request):
        local_only(request)
        from ..shortcuts import install_shortcut

        try:
            return install_shortcut(service.workspace)
        except Exception as exc:
            raise HTTPException(400, f"Couldn't create the shortcut: {exc}") from exc

    @app.post("/api/shutdown")
    def shutdown(request: Request):
        local_only(request)

        def stop():
            if on_shutdown:
                on_shutdown()
            else:
                os.kill(os.getpid(), signal.SIGINT)

        threading.Timer(0.3, stop).start()
        return {"stopping": True}

    # -------------------------------------------------------------- pages
    # The React app (frontend/) builds into STATIC_DIR: index.html + assets/.
    assets = STATIC_DIR / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/")
    def index():
        page = STATIC_DIR / "index.html"
        if not page.exists():
            return Response(_NOT_BUILT_PAGE, media_type="text/html", status_code=500)
        return FileResponse(page, headers={"Cache-Control": "no-store"})

    @app.get("/favicon.svg")
    def favicon():
        return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")

    return app


_NOT_BUILT_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Knowledge Keeper</title></head>
<body style="font:16px system-ui;max-width:560px;margin:15vh auto;padding:0 16px">
<h1 style="font-size:20px">The web interface hasn't been built</h1>
<p>The React frontend's built files are missing from <code>src/knowledge_keeper/web/static</code>.
Build them once with Node.js 20+:</p><pre>cd frontend
npm install
npm run build</pre><p>Then reload this page. (Released copies ship with the build included.)</p></body></html>"""


_LOCKED_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Knowledge Keeper</title>
<style>body{font:16px system-ui,sans-serif;display:grid;place-items:center;min-height:100vh;margin:0;
background:#f6f5f2;color:#1d1c1a}@media(prefers-color-scheme:dark){body{background:#161615;color:#ecebe7}}
form{display:grid;gap:12px;max-width:320px;padding:24px}input,button{font:inherit;padding:10px 12px;
border-radius:8px;border:1px solid #9994}button{background:#2f6f5e;color:#fff;border:0;cursor:pointer}</style>
</head><body><form method="get" action="/"><h1 style="font-size:20px;margin:0">Knowledge Keeper</h1>
<p style="margin:0">Enter the access code shown on the computer that's sharing this knowledge base.</p>
<input name="code" autocomplete="off" autofocus aria-label="Access code" placeholder="Access code">
<button>Open</button></form></body></html>"""
