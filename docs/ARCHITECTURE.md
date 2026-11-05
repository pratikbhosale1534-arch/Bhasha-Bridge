# Architecture & data flow

```mermaid
flowchart LR
  U[Student browser<br/>HTML/CSS/JS] -->|multipart upload| X[/api/extract/]
  subgraph S[FastAPI server - Bhasha Bridge]
    X --> P[extract.py<br/>validate + parse in memory]
    P --> SEC[split_sections]
    T[/api/translate · simplify · glossary/] --> E[translate.py<br/>protect code/math, chunk, prompt, verify]
    E --> L[llm.py<br/>IAM token cache, retry, error mapping]
    H[(SQLite<br/>history: output only, TTL)]
    EX[/api/export<br/>DOCX MD TXT HTML/]
  end
  SEC -->|text + sections| U
  U -->|section text| T
  L -->|API key -> bearer| IAM[IBM Cloud IAM]
  L -->|/ml/v1/text/chat| WX[IBM watsonx.ai Runtime<br/>Granite 4 H Small - Lite plan]
  U -->|save / list| H
  U -->|translated text| EX
```

**Translate flow:** upload → `extract_document` (type sniffing, limits, Markdown output) → sections → per section: `protect()` swaps code/math/URLs for `⟦P#⟧` → chunks ≤2500 chars → Granite chat call (system prompt with preservation rules) → `restore()`; if placeholders are missing, retry once, else re-append and warn → numeric check → ordered reassembly → UI (side by side) → optional history save (output only) → export.

**Trust boundaries:** browser never sees IBM credentials; documents live only in request memory and browser state; only translated output is persisted (SQLite, auto-expiring).

**Demo mode:** if `WATSONX_API_KEY`/`WATSONX_PROJECT_ID` are absent (or `DEMO_MODE=true`) the client never calls IBM; responses carry `provider:"demo"` and a visible banner.
