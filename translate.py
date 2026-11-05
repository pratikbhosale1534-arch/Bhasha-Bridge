"""Translation / simplification / glossary engine (IBM Granite via llm.py)."""
from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field

from llm import GraniteClient, LLMError, get_client, parse_json

CHUNK_CHARS = int(os.environ.get("CHUNK_CHARS", "2500"))
MAX_SECTION_CHARS = int(os.environ.get("MAX_SECTION_CHARS", "20000"))
MAX_DOC_CHARS = int(os.environ.get("MAX_DOC_CHARS", "60000"))

# `official` = listed by IBM as a supported language for Granite 4.0 instruct models.
# Everything else is accepted but flagged "experimental" because quality is not documented by IBM.
LANGUAGES = {
    "en": ("English", True), "hi": ("Hindi", False), "mr": ("Marathi", False),
    "bn": ("Bengali", False), "ta": ("Tamil", False), "te": ("Telugu", False),
    "gu": ("Gujarati", False), "kn": ("Kannada", False), "ml": ("Malayalam", False),
    "pa": ("Punjabi", False), "ur": ("Urdu", False),
    "es": ("Spanish", True), "fr": ("French", True), "de": ("German", True),
    "pt": ("Portuguese", True), "it": ("Italian", True), "nl": ("Dutch", True),
    "ja": ("Japanese", True), "ko": ("Korean", True), "zh": ("Chinese", True),
    "ar": ("Arabic", True), "cs": ("Czech", True),
}


def language_list() -> list:
    return [{"code": k, "name": v[0], "quality": "supported" if v[1] else "experimental"}
            for k, v in LANGUAGES.items()]


def lang_name(code: str) -> str:
    if code not in LANGUAGES:
        raise LLMError(f"Unsupported language code '{code}'.", 400)
    return LANGUAGES[code][0]


def quality_notes(source: str, target: str) -> list:
    notes = []
    for code in (source, target):
        if code in LANGUAGES and not LANGUAGES[code][1]:
            notes.append(f"{LANGUAGES[code][0]} is not on IBM's list of officially supported Granite languages "
                         f"(English, German, Spanish, French, Japanese, Portuguese, Arabic, Czech, Italian, Korean, Dutch, Chinese). "
                         f"Expect more errors and have a fluent speaker review the output.")
    return notes


# ── protection of code / math / URLs ────────────────────────────────────────
_PROTECT = re.compile(
    r"""(```[\s\S]*?```)                 # fenced code
       |(\$\$[\s\S]+?\$\$)               # display math
       |(\\\[[\s\S]+?\\\])               # \[ \]
       |(\\\([\s\S]+?\\\))               # \( \)
       |(?<![\w$])(\$[^\s$][^$\n]*?[^\s$]\$|\$[^\s$]\$)(?![\w$])   # inline $math$
       |(`[^`\n]+`)                      # inline code
       |(https?://[^\s)>\]]*[^\s).,;:!?>\]])   # URLs (no trailing punctuation)
       |([A-Za-z0-9_.]+@[A-Za-z0-9_.-]+\.[A-Za-z]{2,})   # emails
    """, re.X)
_PH = re.compile(r"⟦P(\d+)⟧")


def protect(text: str) -> tuple[str, list]:
    spans: list = []

    def sub(m):
        spans.append(m.group(0))
        return f"⟦P{len(spans) - 1}⟧"

    return _PROTECT.sub(sub, text), spans


def restore(text: str, spans: list) -> tuple[str, list]:
    """Put protected spans back. Returns (text, missing_indices)."""
    found = {int(i) for i in _PH.findall(text)}
    missing = [i for i in range(len(spans)) if i not in found]
    out = _PH.sub(lambda m: spans[int(m.group(1))] if int(m.group(1)) < len(spans) else "", text)
    return out, missing


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def number_mismatch(source: str, output: str) -> bool:
    """True if the multiset of ASCII numbers differs (numbers must be preserved)."""
    a = sorted(_NUM.findall(source))
    b = sorted(_NUM.findall(output))
    return a != b


# ── chunking ────────────────────────────────────────────────────────────────
def split_blocks(text: str) -> list:
    """Split into blocks on blank lines, keeping fenced code blocks intact."""
    blocks, buf, in_code = [], [], False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        if not line.strip() and not in_code:
            if buf:
                blocks.append("\n".join(buf)); buf = []
        else:
            buf.append(line)
    if buf:
        blocks.append("\n".join(buf))
    return blocks


def _split_long(block: str, limit: int) -> list:
    if len(block) <= limit:
        return [block]
    if block.lstrip().startswith("```"):
        return [block]  # never cut code
    parts, cur = [], ""
    for sent in re.split(r"(?<=[.!?।॥。])\s+|\n", block):
        if cur and len(cur) + len(sent) + 1 > limit:
            parts.append(cur); cur = sent
        else:
            cur = f"{cur} {sent}".strip() if cur else sent
    if cur:
        parts.append(cur)
    out = []
    for p in parts:  # hard cut a single endless sentence
        while len(p) > limit * 2:
            out.append(p[:limit]); p = p[limit:]
        out.append(p)
    return out


def chunk_text(text: str, limit: int = CHUNK_CHARS) -> list:
    chunks, cur = [], ""
    for b in split_blocks(text):
        for piece in _split_long(b, limit):
            if cur and len(cur) + len(piece) + 2 > limit:
                chunks.append(cur); cur = piece
            else:
                cur = f"{cur}\n\n{piece}" if cur else piece
    if cur:
        chunks.append(cur)
    return chunks


def split_sections(text: str) -> list:
    """Split Markdown by headings (levels 1-3) into selectable sections."""
    sections, title, buf = [], "Introduction", []
    in_code = False

    def flush():
        body = "\n".join(buf).strip()
        if body:
            sections.append({"id": len(sections), "title": title, "text": body})

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
        m = re.match(r"^(#{1,3})\s+(.*)", line) if not in_code else None
        if m and (buf and any(b.strip() for b in buf)):
            flush(); buf = [line]; title = m.group(2).strip()[:80]
        elif m:
            buf = [line]; title = m.group(2).strip()[:80]
        else:
            buf.append(line)
    flush()
    # Merge tiny sections and cap huge ones so each API call stays reasonable.
    merged = []
    for s in sections:
        if merged and len(s["text"]) < 120 and len(merged[-1]["text"]) + len(s["text"]) < 3000:
            merged[-1]["text"] += "\n\n" + s["text"]
        else:
            merged.append(dict(s))
    final = []
    for s in merged:
        if len(s["text"]) > MAX_SECTION_CHARS:
            for i, c in enumerate(chunk_text(s["text"], MAX_SECTION_CHARS // 2)):
                final.append({"title": f'{s["title"]} (part {i + 1})', "text": c})
        else:
            final.append({"title": s["title"], "text": s["text"]})
    for i, s in enumerate(final):
        s["id"] = i
        s["chars"] = len(s["text"])
    return final


# ── prompts ─────────────────────────────────────────────────────────────────
TRANSLATE_SYSTEM = """You are a professional academic translator for university course material.
Translate the user's text from {src} to {tgt}.

STRICT RULES
1. Output ONLY the translation. No commentary, notes, or explanations.
2. Keep the Markdown structure exactly: headings (#), bullet lists (-), numbered lists, tables (|), bold, line breaks.
3. Tokens that look like ⟦P0⟧, ⟦P1⟧ ... are protected code or math. Copy every one unchanged, in a sensible position. Never translate, remove, or add them.
4. Keep all numbers, units, variable names, chemical formulas, and equations exactly as written. Use ASCII digits (0-9), not native-script digits.
5. For established technical terms, translate them naturally and give the original English term in parentheses on first use, e.g. "inertia" -> "<translation> (inertia)". Keep abbreviations (DNA, CPU, SI) and proper names unchanged.
6. Do not summarize, simplify, or add information. Translate faithfully, sentence by sentence."""

TRANSLATE_RETRY_NOTE = "\n\nIMPORTANT: your previous answer dropped or changed protected tokens. Every ⟦P#⟧ token in the input MUST appear exactly once in the output."

SIMPLIFY_SYSTEM = """You are a patient teacher helping a student understand course material.
Rewrite the text below in {tgt} so it is easy to understand ({level}).

RULES
1. This is an AI-GENERATED SIMPLIFICATION, not a translation. Explain ideas in plain, short sentences.
2. Keep every important fact correct. Do not invent facts, numbers, or examples that change the meaning.
3. Keep key technical terms, but add a short everyday explanation next to each the first time it appears; keep the English term in parentheses if {tgt} is not English.
4. Tokens like ⟦P0⟧ are protected code or math. Copy them unchanged.
5. Use short Markdown (headings and bullets) where it helps. Output only the simplified text."""

LEVELS = {
    "simple": "for a high-school student",
    "very_simple": "for a beginner, like explaining to a 12-year-old with a small everyday example",
}

GLOSSARY_SYSTEM = """You extract a study glossary from course text.
Return ONLY a JSON array (no markdown fences) of up to {n} objects, most important first:
{{"term": "<term exactly as in the source>", "translation": "<term in {tgt}>", "definition": "<one-sentence definition in {tgt}>"}}
Rules: choose technical terms, formulas' names and key concepts only; skip common words; do not invent terms that are not in the text; definitions must be accurate and short."""


@dataclass
class TranslationResult:
    text: str
    provider: str
    model: str | None
    warnings: list = field(default_factory=list)
    chunks: int = 0
    needs_review: bool = False


DEMO_BANNER = "⚠ DEMO MODE: this text was NOT translated by IBM Granite. "


async def _one_chunk(client: GraniteClient, system: str, chunk: str, max_tokens: int, check_numbers: bool):
    protected, spans = protect(chunk)
    warnings = []
    out = ""
    for attempt in range(2):
        sys_prompt = system + (TRANSLATE_RETRY_NOTE if attempt else "")
        out = await client.chat(sys_prompt, [{"role": "user", "content": protected}], max_tokens=max_tokens, temperature=0.1)
        out = out.strip()
        if out.startswith("```") and not chunk.lstrip().startswith("```"):
            out = re.sub(r"^```[a-z]*\n?|\n?```$", "", out).strip()
        restored, missing = restore(out, spans)
        if not missing:
            break
    else:
        restored, missing = restore(out, spans)
    if missing:
        warnings.append(f"{len(missing)} protected code/math item(s) were dropped by the model and re-appended at the end of the block.")
        restored += "\n\n" + "\n".join(spans[i] for i in missing)
    if check_numbers and number_mismatch(chunk, restored):
        warnings.append("Numbers in the output differ from the source. Check figures carefully.")
    return restored, warnings


async def run(mode: str, text: str, source: str, target: str, level: str = "simple",
              client: GraniteClient | None = None) -> TranslationResult:
    """mode: 'translate' | 'simplify'."""
    client = client or get_client()
    text = (text or "").strip()
    if not text:
        raise LLMError("There is no text to process.", 400)
    if len(text) > MAX_SECTION_CHARS * 2:
        raise LLMError(f"This request is too large ({len(text)} chars). Send one section at a time.", 413)
    tgt_name = lang_name(target)
    src_name = lang_name(source) if source != "auto" else "the source language"
    if mode == "translate" and source == target:
        raise LLMError("Source and target languages are the same.", 400)
    warnings = quality_notes("en" if source == "auto" else source, target)
    chunks = chunk_text(text)

    if client.config.is_demo:
        body = "\n\n".join(chunks)
        tag = "translation" if mode == "translate" else "simplification"
        return TranslationResult(f"> {DEMO_BANNER}(Demo {tag} to {tgt_name}; original text shown below.)\n\n{body}",
                                 "demo", None, warnings + ["Demo mode: configure IBM watsonx.ai credentials for real output."],
                                 len(chunks), True)

    if mode == "translate":
        system = TRANSLATE_SYSTEM.format(src=src_name, tgt=tgt_name)
    else:
        system = SIMPLIFY_SYSTEM.format(tgt=tgt_name, level=LEVELS.get(level, LEVELS["simple"]))
    results = []
    for ch in chunks:  # sequential: keeps order and respects Lite rate limits
        est = int(len(ch) / 2.2) + 400  # generous for Indic scripts (more tokens/char)
        out, w = await _one_chunk(client, system, ch, min(max(est, 600), 4000), check_numbers=(mode == "translate"))
        results.append(out)
        warnings += w
    # de-duplicate identical warnings
    seen, uniq = set(), []
    for w in warnings:
        if w not in seen:
            seen.add(w); uniq.append(w)
    return TranslationResult("\n\n".join(results), "ibm-watsonx-ai", client.config.model_id, uniq, len(chunks),
                             needs_review=True)


async def glossary(text: str, target: str, n: int = 12, client: GraniteClient | None = None) -> dict:
    client = client or get_client()
    tgt = lang_name(target)
    if client.config.is_demo:
        return {"provider": "demo", "model": None, "terms": [],
                "warnings": ["Demo mode: glossary needs IBM watsonx.ai credentials."]}
    protected, _ = protect(text[:8000])
    raw = await client.chat(GLOSSARY_SYSTEM.format(n=n, tgt=tgt), [{"role": "user", "content": protected}],
                            max_tokens=1800, temperature=0.1)
    data = parse_json(raw)
    if isinstance(data, dict):
        data = data.get("terms") or data.get("glossary") or []
    terms = []
    low = text.lower()
    for t in data if isinstance(data, list) else []:
        if not isinstance(t, dict) or not t.get("term"):
            continue
        term = str(t["term"]).strip()
        terms.append({"term": term, "translation": str(t.get("translation", "")).strip(),
                      "definition": str(t.get("definition", "")).strip(),
                      "in_source": term.lower() in low})
    warnings = quality_notes("en", target)
    if any(not t["in_source"] for t in terms):
        warnings.append("Some glossary terms do not appear verbatim in the source; verify them.")
    return {"provider": "ibm-watsonx-ai", "model": client.config.model_id, "terms": terms[:n], "warnings": warnings}
