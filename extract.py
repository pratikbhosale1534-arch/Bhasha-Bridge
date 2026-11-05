"""Document validation + text extraction for Bhasha Bridge.

Supported: PDF, DOCX, TXT, PPTX. Uploaded bytes are processed in memory only
and are never written to disk. Output is lightweight Markdown so headings,
lists and tables survive translation.
"""
from __future__ import annotations

import io
import os
import statistics
import zipfile
from dataclasses import dataclass, field

MAX_UPLOAD_BYTES = int(float(os.environ.get("MAX_UPLOAD_MB", "4" if os.environ.get("VERCEL") else "10")) * 1024 * 1024)
MAX_PDF_PAGES = int(os.environ.get("MAX_PDF_PAGES", "200"))
MAX_ZIP_UNCOMPRESSED = 150 * 1024 * 1024  # zip-bomb guard for DOCX/PPTX
ALLOWED_EXTENSIONS = ("pdf", "docx", "txt", "pptx")


class ExtractionError(Exception):
    """User-facing extraction/validation problem (carries an HTTP status)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass
class Extracted:
    filename: str
    file_type: str
    text: str
    pages: int = 0           # pages (PDF) or slides (PPTX); 0 if not applicable
    warnings: list = field(default_factory=list)


def _ext(filename: str) -> str:
    name = (filename or "").strip()
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def validate_upload(filename: str, raw: bytes) -> str:
    """Return the validated file type or raise ExtractionError."""
    ext = _ext(filename)
    if ext not in ALLOWED_EXTENSIONS:
        if ext in ("doc", "ppt"):
            raise ExtractionError(
                f"Legacy .{ext} files are not supported. Please re-save as .{ext}x and upload again.")
        raise ExtractionError(
            f"Unsupported file type '.{ext or '?'}'. Allowed types: PDF, DOCX, PPTX, TXT.")
    if not raw:
        raise ExtractionError("The uploaded file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        mb = MAX_UPLOAD_BYTES / 1024 / 1024
        raise ExtractionError(f"File is too large (limit {mb:g} MB).", 413)

    # Content sniffing: extension alone is not trusted.
    if ext == "pdf" and not raw.lstrip()[:5].startswith(b"%PDF"):
        raise ExtractionError("This file does not look like a valid PDF.")
    if ext in ("docx", "pptx"):
        if not raw.startswith(b"PK"):
            raise ExtractionError(f"This file does not look like a valid .{ext} document.")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                names = set(z.namelist())
                total = sum(i.file_size for i in z.infolist())
        except zipfile.BadZipFile:
            raise ExtractionError(f"This .{ext} file is corrupted or not a real Office document.")
        if total > MAX_ZIP_UNCOMPRESSED:
            raise ExtractionError("This document expands to an unsafe size and was rejected.", 413)
        marker = "word/document.xml" if ext == "docx" else "ppt/presentation.xml"
        if marker not in names:
            raise ExtractionError(f"This file is a ZIP archive but not a valid .{ext} document.")
    if ext == "txt" and b"\x00" in raw[:4096] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise ExtractionError("This file looks binary, not plain text.")
    return ext


# ── TXT ──────────────────────────────────────────────────────────────────────
def _extract_txt(raw: bytes, warnings: list) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            if enc == "utf-16" and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
                continue
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    warnings.append("File was not valid UTF-8; decoded as Windows-1252, some characters may be wrong.")
    return raw.decode("cp1252", errors="replace")


# ── DOCX ─────────────────────────────────────────────────────────────────────
def _extract_docx(raw: bytes, warnings: list) -> str:
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    try:
        doc = Document(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"Could not open this DOCX file: {e}")
    out: list[str] = []
    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(child, doc)
            t = p.text.strip()
            if not t:
                continue
            style = (p.style.name if p.style is not None else "") or ""
            sl = style.lower()
            if sl == "title":
                out.append(f"# {t}")
            elif sl.startswith("heading"):
                digits = "".join(c for c in sl if c.isdigit())
                lvl = min(max(int(digits or 1), 1) + 1, 5)
                out.append("#" * lvl + " " + t)
            elif "list" in sl:
                out.append(f"- {t}")
            else:
                out.append(t)
        elif tag == "tbl":
            tbl = Table(child, doc)
            rows = []
            for r in tbl.rows:
                cells = [c.text.strip().replace("\n", " ").replace("|", "/") for c in r.cells]
                rows.append("| " + " | ".join(cells) + " |")
            if rows:
                ncols = rows[0].count("|") - 1
                rows.insert(1, "|" + "---|" * max(ncols, 1))
                out.append("\n".join(rows))
    return _join_blocks(out)


# ── PPTX ─────────────────────────────────────────────────────────────────────
def _extract_pptx(raw: bytes, warnings: list) -> tuple[str, int]:
    from pptx import Presentation

    try:
        prs = Presentation(io.BytesIO(raw))
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"Could not open this PPTX file: {e}")
    out: list[str] = []
    n = 0
    for n, slide in enumerate(prs.slides, start=1):
        title_shape = slide.shapes.title
        title_id = title_shape.shape_id if title_shape is not None else None
        title = title_shape.text_frame.text.strip() if title_shape is not None and title_shape.has_text_frame else ""
        out.append(f"## Slide {n}" + (f": {title}" if title else ""))
        for shape in slide.shapes:
            if title_id is not None and shape.shape_id == title_id:
                continue
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    t = "".join(r.text for r in para.runs).strip() or para.text.strip()
                    if t:
                        out.append(("  " * min(para.level, 3)) + f"- {t}")
            elif getattr(shape, "has_table", False) and shape.has_table:
                rows = []
                for r in shape.table.rows:
                    rows.append("| " + " | ".join(c.text.strip().replace("|", "/") for c in r.cells) + " |")
                if rows:
                    rows.insert(1, "|" + "---|" * (rows[0].count("|") - 1))
                    out.append("\n".join(rows))
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame is not None:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                out.append(f"*Speaker notes:* {notes}")
    return _join_blocks(out), n


# ── PDF ──────────────────────────────────────────────────────────────────────
def _extract_pdf(raw: bytes, warnings: list) -> tuple[str, int]:
    try:
        import pymupdf as fitz  # PyMuPDF >= 1.24.3
    except ImportError:  # older releases
        import fitz

    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise ExtractionError(f"Could not open this PDF: {e}")
    if doc.needs_pass:
        raise ExtractionError("This PDF is password-protected. Remove the password and try again.")
    npages = len(doc)
    if npages > MAX_PDF_PAGES:
        raise ExtractionError(f"This PDF has {npages} pages; the limit is {MAX_PDF_PAGES}.", 413)

    # Pass 1: collect font sizes to find the body size for heading detection.
    sizes: list[float] = []
    pages_data = []
    for page in doc:
        d = page.get_text("dict")
        pages_data.append(d)
        for b in d.get("blocks", []):
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    if sp["text"].strip():
                        sizes.extend([round(sp["size"], 1)] * max(len(sp["text"].strip()), 1))
    body = statistics.median(sizes) if sizes else 11.0

    out: list[str] = []
    for d in pages_data:
        for b in d.get("blocks", []):
            if b.get("type") != 0:
                continue
            lines = []
            max_size = 0.0
            for ln in b.get("lines", []):
                txt = "".join(sp["text"] for sp in ln.get("spans", [])).strip()
                if txt:
                    lines.append(txt)
                    max_size = max([max_size] + [sp["size"] for sp in ln["spans"] if sp["text"].strip()])
            if not lines:
                continue
            text = " ".join(lines)
            # De-hyphenate line-break hyphenation.
            text = text.replace("- ", "") if text.count("- ") and all(l.endswith("-") for l in lines[:-1]) else text
            if len(text) < 150 and max_size >= body * 1.25:
                lvl = 1 if max_size >= body * 1.6 else 2
                out.append("#" * lvl + " " + text)
            elif lines and lines[0][:1] in "•▪●◦‣" or (len(lines[0]) > 2 and lines[0][:2] in ("- ", "* ")):
                out.extend("- " + l.lstrip("•▪●◦‣-* ").strip() for l in lines)
            else:
                out.append(text)
    text = _join_blocks(out)
    if not text.strip():
        raise ExtractionError(
            "No selectable text found. This looks like a scanned/image-only PDF; OCR is not supported yet.")
    return text, npages


def _join_blocks(blocks: list) -> str:
    """Join blocks with blank lines, but keep consecutive list items tight."""
    res = ""
    prev_list = False
    for b in blocks:
        is_list = b.lstrip().startswith("- ") and "\n" not in b
        if not res:
            res = b
        elif is_list and prev_list:
            res += "\n" + b
        else:
            res += "\n\n" + b
        prev_list = is_list
    return res


# ── Public API ───────────────────────────────────────────────────────────────
def extract_document(filename: str, raw: bytes) -> Extracted:
    ftype = validate_upload(filename, raw)
    warnings: list = []
    pages = 0
    if ftype == "txt":
        text = _extract_txt(raw, warnings)
    elif ftype == "docx":
        text = _extract_docx(raw, warnings)
    elif ftype == "pptx":
        text, pages = _extract_pptx(raw, warnings)
    else:
        text, pages = _extract_pdf(raw, warnings)
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").strip()
    if not text:
        raise ExtractionError("No text could be extracted from this file.")
    return Extracted(filename=os.path.basename(filename), file_type=ftype, text=text, pages=pages, warnings=warnings)
