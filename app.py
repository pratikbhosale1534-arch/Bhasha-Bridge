"""Bhasha Bridge — Multilingual Course Content Translator (IBM SkillsBuild AICTE-2026, PS #17).

FastAPI backend. AI provider: IBM watsonx.ai (Granite) via llm.py.
Uploaded documents are processed in memory and never written to disk.
"""
import io, os, re, sqlite3, time, uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Literal, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import extract as extractor
import translate as tr
from llm import LLMError, get_client

try:  # optional: load .env for local development
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="Bhasha Bridge — Multilingual Course Translator", version="5.0.0")

# ── Basic hardening: security headers + per-IP rate limit on API routes ───────
RATE_LIMIT = int(os.environ.get("RATE_LIMIT_PER_MIN", "40"))
_hits: dict = defaultdict(deque)

@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/api/") and request.method in ("POST", "DELETE"):
        ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "?"))
        now = time.time()
        q = _hits[ip]
        while q and now - q[0] > 60:
            q.popleft()
        if len(q) >= RATE_LIMIT:
            return JSONResponse({"error": "Too many requests. Please wait a minute and try again."}, status_code=429)
        q.append(now)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if request.url.path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp

import logging, traceback
log = logging.getLogger("bhasha")

@app.exception_handler(Exception)
async def unexpected_error_handler(request: Request, exc: Exception):
    ref = uuid.uuid4().hex[:8]
    log.error("Unhandled error ref=%s path=%s\n%s", ref, request.url.path, traceback.format_exc())
    return JSONResponse({"error": f"Unexpected server error (ref {ref}). Check the server logs for this reference."}, status_code=500)

@app.exception_handler(LLMError)
async def llm_error_handler(request: Request, exc: LLMError):
    return JSONResponse({"error": exc.message}, status_code=exc.status)


# ── SQLite (history only) ─────────────────────────────────────────────────────
# On Vercel the project folder is read-only; only /tmp is writable (and it is temporary).
ON_VERCEL = bool(os.environ.get("VERCEL"))
DB_PATH = os.environ.get("APP_DB") or ("/tmp/bhasha.db" if ON_VERCEL else "bhasha.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

# ── /api/status, /api/languages ──────────────────────────────────────────────
@app.get("/api/status")
async def status():
    return {"ai": get_client().status(), "limits": {
        "max_upload_mb": extractor.MAX_UPLOAD_BYTES / 1024 / 1024,
        "max_pdf_pages": extractor.MAX_PDF_PAGES, "max_doc_chars": tr.MAX_DOC_CHARS,
        "allowed_types": list(extractor.ALLOWED_EXTENSIONS)},
        "history_retention_days": HISTORY_DAYS, "history_persistent": not ON_VERCEL}

@app.get("/api/languages")
async def languages():
    return {"languages": tr.language_list()}

# ── /api/extract  (upload → text + sections; nothing is stored) ──────────────
async def _read_upload(file: UploadFile) -> bytes:
    raw = await file.read(extractor.MAX_UPLOAD_BYTES + 1)
    if len(raw) > extractor.MAX_UPLOAD_BYTES:
        raise extractor.ExtractionError(f"File is too large (limit {extractor.MAX_UPLOAD_BYTES / 1024 / 1024:g} MB).", 413)
    return raw

@app.post("/api/extract")
async def extract_endpoint(file: UploadFile = File(...)):
    try:
        raw = await _read_upload(file)
        doc = extractor.extract_document(file.filename or "", raw)
    except extractor.ExtractionError as e:
        return JSONResponse({"error": e.message}, status_code=e.status)
    except Exception:  # noqa: BLE001 - never leak internals
        return JSONResponse({"error": "Could not read this file. It may be corrupted."}, status_code=422)
    warnings = list(doc.warnings)
    text = doc.text
    if len(text) > tr.MAX_DOC_CHARS:
        warnings.append(f"Document is long ({len(text):,} characters). Only the first {tr.MAX_DOC_CHARS:,} are loaded.")
        text = text[:tr.MAX_DOC_CHARS]
    return {"filename": doc.filename, "file_type": doc.file_type, "pages": doc.pages,
            "chars": len(text), "text": text, "sections": tr.split_sections(text), "warnings": warnings}

# ── Translation / simplification / glossary ──────────────────────────────────
class ProcessReq(BaseModel):
    text: str = Field(min_length=1, max_length=tr.MAX_SECTION_CHARS * 2)
    source_lang: str = "en"
    target_lang: str = "hi"
    level: Literal["simple", "very_simple"] = "simple"

def _result_json(r: tr.TranslationResult, kind: str) -> dict:
    return {"kind": kind, "text": r.text, "provider": r.provider, "model": r.model, "chunks": r.chunks,
            "warnings": r.warnings, "needs_human_review": r.needs_review,
            "disclaimer": ("AI translation: may contain errors. Have a fluent speaker verify it before relying on it."
                           if kind == "translation" else
                           "AI-generated simplification, NOT a translation. It re-explains the content and may omit detail or contain errors.")}

@app.post("/api/translate")
async def translate_endpoint(req: ProcessReq):
    r = await tr.run("translate", req.text, req.source_lang, req.target_lang)
    return _result_json(r, "translation")

@app.post("/api/simplify")
async def simplify_endpoint(req: ProcessReq):
    r = await tr.run("simplify", req.text, req.source_lang, req.target_lang, req.level)
    return _result_json(r, "simplification")

class GlossaryReq(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    target_lang: str = "hi"
    max_terms: int = Field(default=12, ge=3, le=25)

@app.post("/api/glossary")
async def glossary_endpoint(req: GlossaryReq):
    return await tr.glossary(req.text, req.target_lang, req.max_terms)

# ── Export (TXT / Markdown / HTML / DOCX) ────────────────────────────────────
class ExportReq(BaseModel):
    title: str = Field(default="translation", max_length=120)
    translated_text: str = Field(min_length=1, max_length=400_000)
    original_text: Optional[str] = Field(default=None, max_length=400_000)
    format: Literal["txt", "md", "html", "docx"] = "docx"
    kind: Literal["translation", "simplification"] = "translation"
    target_lang: str = "hi"
    glossary: list = Field(default_factory=list, max_length=60)

NOTICE = {"translation": "AI-generated translation (IBM Granite via watsonx.ai). Human review is recommended before use.",
          "simplification": "AI-generated SIMPLIFICATION (not a translation). Verify against the original material."}

def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s).strip("._")[:60] or "bhasha"

def _md_to_html(md: str) -> str:
    import html
    out, in_code, in_ul = [], False, False
    for line in md.split("\n"):
        if line.strip().startswith("```"):
            out.append("</pre>" if in_code else "<pre>"); in_code = not in_code; continue
        if in_code:
            out.append(html.escape(line)); continue
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        li = re.match(r"^\s*[-*]\s+(.*)", line)
        if in_ul and not li:
            out.append("</ul>"); in_ul = False
        if m:
            out.append(f"<h{len(m.group(1))}>{html.escape(m.group(2))}</h{len(m.group(1))}>")
        elif li:
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{html.escape(li.group(1))}</li>")
        elif line.strip().startswith("|"):
            if re.match(r"^\|[\s:|-]+\|$", line.strip()):
                continue
            cells = [html.escape(c.strip()) for c in line.strip().strip("|").split("|")]
            out.append("<p class='row'>" + " &nbsp;|&nbsp; ".join(cells) + "</p>")
        elif line.strip():
            out.append(f"<p>{html.escape(line)}</p>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)

def _build_docx(req: ExportReq) -> bytes:
    from docx import Document
    from docx.shared import Pt
    d = Document()
    p = d.add_paragraph(); r = p.add_run(NOTICE[req.kind]); r.italic = True; r.font.size = Pt(9)
    in_code = False
    for line in req.translated_text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code; continue
        if in_code:
            rr = d.add_paragraph().add_run(line); rr.font.name = "Courier New"; rr.font.size = Pt(9); continue
        m = re.match(r"^(#{1,4})\s+(.*)", line)
        li = re.match(r"^\s*[-*]\s+(.*)", line)
        if m:
            d.add_heading(m.group(2), level=min(len(m.group(1)), 4))
        elif li:
            d.add_paragraph(li.group(1), style="List Bullet")
        elif line.strip().startswith("|"):
            if re.match(r"^\|[\s:|-]+\|$", line.strip()):
                continue
            d.add_paragraph(" | ".join(c.strip() for c in line.strip().strip("|").split("|")))
        elif line.strip():
            d.add_paragraph(line.lstrip("> ").strip() if line.startswith(">") else line)
    if req.glossary:
        d.add_heading("Glossary", level=1)
        t = d.add_table(rows=1, cols=3); t.style = "Table Grid"
        for i, h in enumerate(("Term", "Translation", "Definition")):
            t.rows[0].cells[i].text = h
        for g in req.glossary:
            row = t.add_row().cells
            row[0].text = str(g.get("term", "")); row[1].text = str(g.get("translation", "")); row[2].text = str(g.get("definition", ""))
    buf = io.BytesIO(); d.save(buf); return buf.getvalue()

@app.post("/api/export")
async def export_endpoint(req: ExportReq):
    base = _safe_name(req.title)
    body = f"{NOTICE[req.kind]}\n\n{req.translated_text}"
    if req.glossary:
        body += "\n\n## Glossary\n" + "\n".join(
            f"- **{g.get('term','')}** → {g.get('translation','')}: {g.get('definition','')}" for g in req.glossary)
    if req.format == "docx":
        data, mime = _build_docx(req), "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif req.format == "html":
        html_doc = ("<!doctype html><html lang='" + req.target_lang + "'><head><meta charset='utf-8'>"
                    "<meta name='viewport' content='width=device-width,initial-scale=1'><title>" + base + "</title>"
                    "<style>body{font-family:system-ui,'Noto Sans Devanagari',sans-serif;max-width:760px;margin:2rem auto;padding:0 1rem;line-height:1.7}"
                    "pre{background:#f4f4f4;padding:1rem;overflow:auto}.note{background:#fff7e0;border:1px solid #f0d080;padding:.6rem 1rem;border-radius:8px}</style></head><body>"
                    "<p class='note'>" + NOTICE[req.kind] + "</p>" + _md_to_html(req.translated_text) + "</body></html>")
        data, mime = html_doc.encode("utf-8"), "text/html; charset=utf-8"
    else:
        data, mime = body.encode("utf-8"), ("text/markdown; charset=utf-8" if req.format == "md" else "text/plain; charset=utf-8")
    return Response(data, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{base}.{req.format}"'})

# ── Sample documents ─────────────────────────────────────────────────────────
SAMPLES_DIR = os.path.join(BASE_DIR, "samples")
SAMPLE_FILES = {"newton_laws.txt": "Physics: Newton's Laws (TXT)", "photosynthesis.docx": "Biology: Photosynthesis (DOCX)",
                "algorithms.pptx": "Computer Science: Algorithms (PPTX)", "ohms_law.pdf": "Electricity: Ohm's Law (PDF)"}

@app.get("/api/samples")
async def samples():
    return {"samples": [{"name": k, "title": v} for k, v in SAMPLE_FILES.items() if os.path.exists(os.path.join(SAMPLES_DIR, k))]}

@app.get("/api/samples/{name}")
async def sample_extract(name: str):
    if name not in SAMPLE_FILES:
        return JSONResponse({"error": "Unknown sample."}, status_code=404)
    path = os.path.join(SAMPLES_DIR, name)
    with open(path, "rb") as f:
        doc = extractor.extract_document(name, f.read())
    return {"filename": doc.filename, "file_type": doc.file_type, "pages": doc.pages, "chars": len(doc.text),
            "text": doc.text, "sections": tr.split_sections(doc.text), "warnings": doc.warnings}

# ── History (translated output only; originals are NOT stored) ───────────────
HISTORY_DAYS = int(os.environ.get("HISTORY_RETENTION_DAYS", "7"))

def init_history():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS history (
        id TEXT PRIMARY KEY, created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
        filename TEXT NOT NULL, kind TEXT NOT NULL, source_lang TEXT NOT NULL, target_lang TEXT NOT NULL,
        provider TEXT NOT NULL, model TEXT, chars INTEGER NOT NULL, translated_text TEXT NOT NULL)""")
    conn.commit(); conn.close()

def purge_history(conn):
    conn.execute("DELETE FROM history WHERE expires_at < ?", (datetime.now(timezone.utc).isoformat(),))
    conn.commit()

init_history()

class HistorySave(BaseModel):
    filename: str = Field(max_length=200)
    kind: Literal["translation", "simplification"] = "translation"
    source_lang: str = "en"
    target_lang: str
    provider: str
    model: Optional[str] = None
    translated_text: str = Field(min_length=1, max_length=400_000)

@app.post("/api/history")
async def history_save(req: HistorySave):
    conn = get_db(); purge_history(conn)
    now = datetime.now(timezone.utc)
    hid = uuid.uuid4().hex[:12]
    conn.execute("INSERT INTO history VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                 (hid, now.isoformat(), (now + timedelta(days=HISTORY_DAYS)).isoformat(), req.filename, req.kind,
                  req.source_lang, req.target_lang, req.provider, req.model, len(req.translated_text), req.translated_text))
    conn.commit(); conn.close()
    return {"id": hid, "expires_in_days": HISTORY_DAYS}

@app.get("/api/history")
async def history_list():
    conn = get_db(); purge_history(conn)
    rows = conn.execute("SELECT id,created_at,expires_at,filename,kind,source_lang,target_lang,provider,model,chars FROM history ORDER BY created_at DESC LIMIT 50").fetchall()
    conn.close()
    return {"items": [dict(r) for r in rows], "retention_days": HISTORY_DAYS}

@app.get("/api/history/{hid}")
async def history_get(hid: str):
    conn = get_db(); row = conn.execute("SELECT * FROM history WHERE id=?", (hid,)).fetchone(); conn.close()
    if not row:
        return JSONResponse({"error": "History item not found or expired."}, status_code=404)
    return dict(row)

@app.delete("/api/history/{hid}")
async def history_delete(hid: str):
    conn = get_db(); cur = conn.execute("DELETE FROM history WHERE id=?", (hid,)); conn.commit(); conn.close()
    return {"deleted": cur.rowcount}

@app.delete("/api/history")
async def history_clear():
    conn = get_db(); cur = conn.execute("DELETE FROM history"); conn.commit(); conn.close()
    return {"deleted": cur.rowcount}

# ── Static files (MUST be last) ───────────────────────────────────────────────
# On Vercel, public/ is served by the CDN and may not exist inside the function bundle,
# so only mount it when present (local runs / Docker) instead of crashing at startup.
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
if os.path.isdir(PUBLIC_DIR):
    app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")
