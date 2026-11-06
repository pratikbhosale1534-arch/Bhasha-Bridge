# Bhasha Bridge — Multilingual Course Translator

Translate course documents between English, Hindi, Marathi and more, with **IBM Granite on watsonx.ai**.
Built for **IBM SkillsBuild – AICTE-2026, Problem Statement #17: Multi-language Course Content Translator Agent**.

**Live demo:** https://bhasha-bridge-ten.vercel.app/

## What it does
- Upload **PDF, DOCX, PPTX or TXT** study documents and extract the text
- Translate between **22 languages**, including Hindi and Marathi
- Keeps **code, math, URLs, numbers, headings and lists** intact
- **Side-by-side** original and translation; translate all or selected sections
- **Simplify** hard sections in plain language (labelled "AI simplification, not a translation")
- **Glossary** of key terms, **history**, and downloads as **DOCX / MD / TXT / HTML**
- Clearly labelled **demo mode** when no IBM credentials are set

## Tech stack
Python **FastAPI** · HTML / CSS / JavaScript · SQLite (history only) · PyMuPDF, python-docx, python-pptx
**IBM:** Granite 4 H Small (`ibm/granite-4-h-small`) on watsonx.ai Runtime (Lite), IBM Cloud IAM, watsonx.ai Studio

## Run locally
```bash
python -m venv .venv
.venv\Scripts\activate            # Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # Windows: copy .env.example .env
uvicorn app:app --reload --port 8000
```
Open http://localhost:8000. A green banner means IBM Granite is connected; orange means demo mode.

## Environment variables (`.env`)
```
WATSONX_API_KEY=your-ibm-cloud-api-key
WATSONX_PROJECT_ID=your-watsonx-project-id
WATSONX_URL=https://eu-de.ml.cloud.ibm.com     # must match your project's region
WATSONX_MODEL_ID=ibm/granite-4-h-small
```
Never commit `.env`. Keys stay on the server and are never sent to the browser.

## Deploy on Vercel
1. Push the repo to GitHub and import it in Vercel.
2. Add the four environment variables above.
3. Deploy, then check `/api/status` shows `"mode": "granite"`.

On Vercel, uploads are limited to 4 MB and history is temporary.

## Tests
```bash
pip install -r requirements-dev.txt
python -m pytest tests -q
```

## Responsible AI
- Every result carries a **human-review notice**; AI output can contain mistakes
- Hindi, Marathi and other Indian languages are **not on IBM's official Granite language list**, so they are marked experimental
- Uploaded files are processed in memory and **not saved**; history keeps translated output only and auto-deletes
- Text you translate is sent to IBM watsonx.ai, so don't upload confidential material

## Limitations
No OCR (scanned PDFs are not supported) · Lite plan has limited monthly tokens · no user login

## Project structure
```
app.py  extract.py  translate.py  llm.py   backend
public/                                    web interface
samples/  tests/  docs/                    samples, tests, documentation
```
