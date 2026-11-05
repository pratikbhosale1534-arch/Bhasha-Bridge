# Bhasha Bridge — Multilingual Course Translator
IBM SkillsBuild AICTE-2026 · Problem Statement #17 · Multi-language Course Content Translator Agent

Upload a PDF / DOCX / PPTX / TXT course document, translate it between English, Hindi, Marathi and other languages with **IBM Granite on watsonx.ai**, read original and translation side by side, get a student-friendly simplification and a glossary, and download the result. This project does one job: **course content translation**.

> **Status honesty:** the IBM integration follows IBM's published REST API, but it was **not tested against a live IBM account** while building (no credentials/egress in the build environment). Tests use a mock of the IBM HTTP API and a local fake server. Run the 5-minute **Live IBM check** below before you present.

## Quick start
```bash
cd bhasha_bridge
python -m venv .venv && source .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # then edit .env (see "IBM setup")
uvicorn app:app --reload --port 8000
# open http://localhost:8000
```
No credentials? It still runs in **labelled DEMO MODE** (orange banner; output is placeholder text, never presented as Granite).

Docker: `docker build -t bhasha-bridge . && docker run -p 10000:10000 --env-file .env bhasha-bridge`

## IBM setup (Granite + IBM Cloud Lite)
1. Create a free IBM Cloud account → catalog → **watsonx.ai Studio** and **watsonx.ai Runtime** (choose the **Lite** plan; both must be in the same region).
2. Open watsonx.ai → create a **project** → *Manage → Access control/Services & integrations*: associate your **watsonx.ai Runtime** instance. Copy the **Project ID** (*Manage → General*).
3. IBM Cloud → *Manage → Access (IAM) → API keys → Create*. Copy the key.
4. Put them in `.env`: `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, and `WATSONX_URL` = the regional endpoint of your Runtime instance (e.g. `https://us-south.ml.cloud.ibm.com`, `https://eu-de.ml.cloud.ibm.com`).
5. Default model `ibm/granite-4-h-small` (131,072-token context). Change with `WATSONX_MODEL_ID`.

### Live IBM check (do this first)
```bash
python - <<'PY'
import asyncio; from dotenv import load_dotenv; load_dotenv()
from llm import get_client
print(asyncio.run(get_client().chat("You translate.", [{"role":"user","content":"Translate to French: Hello, world."}], 50)))
PY
```
If it prints French → the integration works with your account. Typical failures are mapped to clear messages (401 bad key, 403 wrong project / Runtime not associated, 404 wrong region or model, 429 quota/rate limit).

### IBM services used
| Service | Use | Plan |
|---|---|---|
| IBM watsonx.ai Runtime | Granite inference via `POST {WATSONX_URL}/ml/v1/text/chat?version=2024-05-31` | Lite (free) |
| IBM watsonx.ai Studio | Holds the project (Project ID) | Lite |
| IBM Cloud IAM | API key → bearer token (`https://iam.cloud.ibm.com/identity/token`) | included |

**Lite quota (verify in your account):** IBM's own pages disagree — the plan table shows **50,000** foundation-model tokens/month in some doc versions and **300,000** in others/the developer quick-start; 2 inference requests/second; 20 CUH. Plan for the smaller number: a 5-page document ≈ 10–20k tokens round-trip, so budget demos. The app throttles to 2 concurrent requests and retries 429s with back-off.

**Language caveat:** IBM lists Granite 4.0 language support as English, German, Spanish, French, Japanese, Portuguese, Arabic, Czech, Italian, Korean, Dutch, Chinese. **Hindi, Marathi and other Indian languages are not on that list** — the UI labels them *experimental* and always recommends human review. Don't claim production-grade Marathi quality.

## Features → where implemented
| Requirement | Implementation |
|---|---|
| Upload PDF/DOCX/TXT/PPTX, validation, size limits | `extract.py` (magic-byte sniffing, 10 MB, 200 pages, zip-bomb guard, password/scanned-PDF errors) |
| Display extracted text | `/api/extract` → sections shown in workspace |
| Translate EN/HI/MR/+19 languages | `translate.py` + `llm.py` (Granite) |
| Preserve code/math/URLs/numbers/headings/lists | Placeholders `⟦P#⟧` for code, `$math$`, URLs; Markdown structure in prompt; retry if a placeholder is dropped; number-mismatch warning |
| Side by side, per-section translate, download | `static/translator.js`; `/api/export` → DOCX/MD/TXT/HTML |
| Simplification mode (separate from translation) | `/api/simplify` — labelled “AI simplification — not a translation” |
| Glossary | `/api/glossary` (terms flagged ⚠ if not verbatim in the source) |
| History, errors, loading, samples | `/api/history*` (SQLite, translated output only, auto-expiry), progress bar, 4 sample files in `samples/` |

## API (all JSON unless noted; errors are `{"error": "message"}`)
| Method & path | Purpose |
|---|---|
| `GET /api/status` | AI mode (`granite`/`demo`), model, limits (no secrets) |
| `GET /api/languages` | Languages + `supported`/`experimental` |
| `POST /api/extract` (multipart `file`) | → `{filename,file_type,pages,chars,text,sections[],warnings[]}` (file not stored) |
| `POST /api/translate` `{text,source_lang,target_lang}` | → `{text,provider,model,warnings[],needs_human_review,disclaimer}` |
| `POST /api/simplify` `{text,target_lang,level}` | same shape, `kind:"simplification"` |
| `POST /api/glossary` `{text,target_lang,max_terms}` | → `{terms:[{term,translation,definition,in_source}],warnings}` |
| `POST /api/export` `{translated_text,format,kind,glossary[]}` | file download (docx/md/txt/html) |
| `GET/POST/DELETE /api/history`, `GET/DELETE /api/history/{id}` | translated-output history |
| `GET /api/samples`, `GET /api/samples/{name}` | built-in sample docs |

Status codes: 400 invalid input/type · 413 too large · 422 unreadable file/validation · 429 rate/quota · 503 AI demo/unreachable · 401/403/404 IBM config problems.

## Deploy on Vercel
1. Push the repo to GitHub (never commit `.env`; see `.gitignore`).
2. Vercel → Add New → Project → import the repo. Framework preset: **FastAPI/Other**; leave build settings empty (Vercel detects `app.py`).
3. Settings → Environment Variables: `WATSONX_API_KEY`, `WATSONX_PROJECT_ID`, `WATSONX_URL`, `WATSONX_MODEL_ID`.
4. Deploy, then open `/api/status` — it must show `"mode": "granite"`.

Vercel limits: request bodies max 4.5 MB (uploads are capped at 4 MB there), the file system is temporary (history is stored in `/tmp` and can vanish), static files live in `public/`.

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest tests -q                 # 56 unit/API tests (mock IBM transport; no network)
python tests/e2e.py demo && python tests/e2e.py mock   # browser E2E (needs Playwright + Chromium)
```
`mock` mode runs against `tests/fake_watsonx.py`, a **local fake — not IBM**.

## Privacy & Responsible AI
- **Translation can be wrong.** Every result shows a review notice; experimental languages get an extra warning; number changes and dropped code/math are flagged.
- **Translation ≠ simplification.** Different endpoint, labels and download notice.
- **Uploaded files** are read in memory and discarded; they are never written to disk. **Text you translate is sent to IBM watsonx.ai** — don't upload confidential material. Check IBM's terms for data handling on your plan.
- **History** keeps only translated/simplified output (not originals), default 7 days (`HISTORY_RETENTION_DAYS`), user-deletable.
- **Secrets:** read from server env only; never logged or sent to the browser (a test checks the frontend).
- Basic per-IP rate limit (`RATE_LIMIT_PER_MIN`) protects your Lite quota; output is rendered via `textContent` (no HTML injection).
- No login: history is shared by everyone who can reach the server. Fine for a demo; add auth before multi-user use.

## Scope
Only the translator workflow is included. The earlier tutor, quiz, flashcards, concept map, Feynman check, exam planner, ELI5, voice quiz, YouTube summarizer and mentor dashboard were **removed** to keep the project focused on Problem Statement #17 (and their old Groq calls with them).

## Known limitations
Scanned/image PDFs (no OCR), legacy `.doc/.ppt`, PDF multi-column layouts/formulas rendered as images, no PDF export, Indian-language quality, not tested live on IBM (see above). Documents beyond `MAX_DOC_CHARS` (60,000) are truncated with a warning.

More: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/DEMO_SCRIPT.md`](docs/DEMO_SCRIPT.md) · [`docs/PRESENTATION_OUTLINE.md`](docs/PRESENTATION_OUTLINE.md) · [`docs/SUBMISSION_CHECKLIST.md`](docs/SUBMISSION_CHECKLIST.md)
