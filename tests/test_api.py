import io, os, pytest
from fastapi.testclient import TestClient
import app as appmod, llm
from llm import GraniteClient, Config

S = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")
@pytest.fixture(autouse=True)
def demo_client():
    llm.reset_client(GraniteClient(Config()))  # no credentials -> demo
    appmod._hits.clear(); yield; llm.reset_client(None)
c = TestClient(appmod.app)

def test_status_reports_demo_honestly():
    j = c.get("/api/status").json()
    assert j["ai"]["mode"] == "demo" and "NOT from IBM Granite" in j["ai"]["message"] and j["ai"]["model"] is None

def test_languages_flags_hindi_marathi_experimental():
    l = {x["code"]: x["quality"] for x in c.get("/api/languages").json()["languages"]}
    assert l["hi"] == l["mr"] == "experimental" and l["en"] == "supported"

@pytest.mark.parametrize("n", ["newton_laws.txt", "photosynthesis.docx", "algorithms.pptx", "ohms_law.pdf"])
def test_extract_endpoint(n):
    r = c.post("/api/extract", files={"file": (n, open(os.path.join(S, n), "rb").read())})
    j = r.json(); assert r.status_code == 200 and j["chars"] > 50 and j["sections"] and j["sections"][0]["id"] == 0

def test_extract_errors():
    assert c.post("/api/extract", files={"file": ("x.exe", b"MZ")}).status_code == 400
    assert c.post("/api/extract", files={"file": ("x.pdf", b"junk")}).json()["error"]
    big = b"a" * (11 * 1024 * 1024)
    assert c.post("/api/extract", files={"file": ("x.txt", big)}).status_code == 413

def test_translate_demo_is_labelled():
    j = c.post("/api/translate", json={"text": "Force is mass times acceleration.", "source_lang": "en", "target_lang": "mr"}).json()
    assert j["provider"] == "demo" and "DEMO MODE" in j["text"] and j["needs_human_review"] and "AI translation" in j["disclaimer"]
    assert any("not on IBM's list" in w for w in j["warnings"])

def test_simplify_is_distinct_from_translate():
    j = c.post("/api/simplify", json={"text": "Entropy increases.", "target_lang": "en", "source_lang": "en"}).json()
    assert j["kind"] == "simplification" and "NOT a translation" in j["disclaimer"]

def test_validation():
    assert c.post("/api/translate", json={"text": "", "target_lang": "hi"}).status_code == 422
    assert c.post("/api/translate", json={"text": "x", "target_lang": "xx"}).status_code == 400
    assert c.post("/api/translate", json={"text": "x", "source_lang": "hi", "target_lang": "hi"}).status_code == 400


@pytest.mark.parametrize("fmt,sig", [("docx", b"PK"), ("md", b"AI-generated"), ("txt", b"AI-generated"), ("html", b"<!doctype html>")])
def test_export_formats(fmt, sig):
    r = c.post("/api/export", json={"title": "My Doc!", "translated_text": "# शीर्षक\n\n- बिंदु\n\n```py\nx=1\n```", "format": fmt,
                                     "glossary": [{"term": "a", "translation": "b", "definition": "c"}]})
    assert r.status_code == 200 and sig in r.content[:300]
    assert 'filename="My_Doc.' in r.headers["content-disposition"]

def test_docx_export_contains_devanagari():
    from docx import Document
    r = c.post("/api/export", json={"translated_text": "# शीर्षक\n\nपाठ", "format": "docx"})
    txt = "\n".join(p.text for p in Document(io.BytesIO(r.content)).paragraphs)
    assert "शीर्षक" in txt and "AI-generated" in txt

def test_history_crud_and_privacy():
    r = c.post("/api/history", json={"filename": "a.pdf", "target_lang": "hi", "provider": "demo", "translated_text": "x"}).json()
    hid = r["id"]; assert any(i["id"] == hid for i in c.get("/api/history").json()["items"])
    assert "translated_text" in c.get(f"/api/history/{hid}").json()
    assert "original" not in " ".join(c.get(f"/api/history/{hid}").json().keys())
    assert c.delete(f"/api/history/{hid}").json()["deleted"] == 1 and c.get(f"/api/history/{hid}").status_code == 404

def test_history_expiry():
    conn = appmod.get_db()
    conn.execute("INSERT INTO history VALUES ('old','2000-01-01T00:00:00+00:00','2000-01-02T00:00:00+00:00','o.txt','translation','en','hi','demo',NULL,1,'x')")
    conn.commit(); conn.close()
    assert all(i["id"] != "old" for i in c.get("/api/history").json()["items"])

def test_samples():
    s = c.get("/api/samples").json()["samples"]; assert len(s) == 4
    assert c.get(f"/api/samples/{s[0]['name']}").json()["text"]
    assert c.get("/api/samples/../app.py").status_code in (404, 405) and c.get("/api/samples/evil.txt").status_code == 404

def test_rate_limit(monkeypatch):
    monkeypatch.setattr(appmod, "RATE_LIMIT", 3)
    codes = [c.post("/api/translate", json={"text": "x", "target_lang": "hi"}).status_code for _ in range(5)]
    assert codes[-1] == 429


def test_frontend_assets_resolve():
    assert c.get("/").status_code == 200 and c.get("/style.css").status_code == 200 and c.get("/translator.js").status_code == 200
    html = c.get("/").text
    import re
    for ref in re.findall(r'(?:href|src)="([^"]+\.(?:css|js))"', html):
        if not ref.startswith("http"): assert c.get("/" + ref).status_code == 200, ref

def test_no_secrets_in_frontend():
    for f in ("translator.js", "index.html"):
        t = open(os.path.join(os.path.dirname(S), "public", f), encoding="utf-8").read().lower()
        assert "bearer " not in t and "authorization" not in t and "iam.cloud.ibm.com" not in t and "api.anthropic" not in t

def test_removed_features_are_gone():
    for path in ("/api/chat", "/api/stream", "/api/feynman", "/api/panic", "/api/conceptmap", "/api/eli5",
                 "/api/voice-check", "/api/youtube", "/api/upload", "/api/mentor/students"):
        assert c.post(path, json={}).status_code in (404, 405), path
    assert "Groq" not in open(os.path.join(os.path.dirname(S), "app.py"), encoding="utf-8").read()
