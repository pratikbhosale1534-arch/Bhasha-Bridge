import asyncio, httpx, json, pytest
import translate as t, llm
from llm import GraniteClient, Config, LLMError

def run(c): return asyncio.run(c)

def test_protect_restore_roundtrip():
    src = "Force $F = ma$, code `x=1`, see https://x.org/a.\n\n```py\nprint(1)\n```"
    p, spans = t.protect(src)
    assert "$F" not in p and "print" not in p
    out, missing = t.restore(p, spans)
    assert out == src and not missing

def test_missing_placeholder_detected():
    p, spans = t.protect("a `b` c `d`")
    _, missing = t.restore(p.replace("⟦P1⟧", ""), spans)
    assert missing == [1]

def test_number_mismatch():
    assert not t.number_mismatch("2.5 kg and 10", "10 और 2.5 kg") and t.number_mismatch("2.5", "2.6")

def test_chunking_keeps_code_and_limit():
    text = "para one.\n\n```py\n" + "x=1\n" * 50 + "```\n\n" + ("Sentence number one. " * 200)
    chunks = t.chunk_text(text, 800)
    assert all("```" not in c or c.count("```") % 2 == 0 for c in chunks)
    assert "\n\n".join(chunks).count("x=1") == 50 and len(chunks) > 3

def test_sections_split_on_headings():
    s = t.split_sections("# A\n\n" + "a " * 150 + "\n\n## B\n\n" + "b " * 150)
    assert [x["title"] for x in s] == ["A", "B"] and all("id" in x for x in s)

def test_language_flags():
    langs = {l["code"]: l["quality"] for l in t.language_list()}
    assert langs["en"] == "supported" and langs["hi"] == "experimental" and langs["mr"] == "experimental"
    assert t.quality_notes("en", "mr")

# ---- Granite client logic against a MOCK of the watsonx.ai HTTP API (not a live IBM test) ----
def mock_client(handler, **cfg):
    c = Config(api_key="SECRET-KEY-123", project_id="PROJ-456", base_url="https://example.invalid", **cfg)
    return GraniteClient(c, transport=httpx.MockTransport(handler))

def iam_ok(): return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
def chat_ok(text): return httpx.Response(200, json={"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}})

def test_mock_request_shape_and_usage():
    seen = {}
    def h(req):
        if "identity/token" in str(req.url):
            seen["iam_body"] = req.content.decode(); return iam_ok()
        seen["url"] = str(req.url); seen["auth"] = req.headers["authorization"]; seen["body"] = json.loads(req.content)
        return chat_ok("hello")
    c = mock_client(h)
    assert run(c.chat("sys", [{"role": "user", "content": "hi"}])) == "hello"
    assert "grant_type=urn%3Aibm%3Aparams%3Aoauth%3Agrant-type%3Aapikey" in seen["iam_body"]
    assert seen["url"].endswith("/ml/v1/text/chat?version=2024-05-31") and seen["auth"] == "Bearer tok"
    assert seen["body"]["model_id"] == "ibm/granite-4-h-small" and seen["body"]["project_id"] == "PROJ-456"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "sys"}
    assert c.usage.total == 15
    assert "SECRET-KEY-123" not in json.dumps(c.status()) and "PROJ-456" not in json.dumps(c.status())

def test_mock_429_retries_then_succeeds(monkeypatch):
    async def nosleep(_): pass
    monkeypatch.setattr(llm.asyncio, "sleep", nosleep)
    n = {"c": 0}
    def h(req):
        if "identity" in str(req.url): return iam_ok()
        n["c"] += 1
        return httpx.Response(429, json={"errors": [{"message": "rate"}]}) if n["c"] < 3 else chat_ok("ok")
    assert run(mock_client(h).chat("", [{"role": "user", "content": "x"}])) == "ok" and n["c"] == 3

@pytest.mark.parametrize("status,frag", [(403, "WATSONX_PROJECT_ID"), (404, "WATSONX_URL"), (400, "rejected")])
def test_mock_error_mapping(status, frag):
    def h(req): return iam_ok() if "identity" in str(req.url) else httpx.Response(status, json={"errors": [{"message": "m"}]})
    with pytest.raises(LLMError) as e: run(mock_client(h).chat("", [{"role": "user", "content": "x"}]))
    assert frag in e.value.message and e.value.status == status

def test_mock_bad_api_key():
    with pytest.raises(LLMError) as e: run(mock_client(lambda r: httpx.Response(400, json={})).chat("", [{"role": "user", "content": "x"}]))
    assert e.value.status == 401 and "API key" in e.value.message

def test_translate_retries_when_placeholder_dropped():
    calls = []
    def h(req):
        if "identity" in str(req.url): return iam_ok()
        sysm = json.loads(req.content)["messages"][0]["content"]
        calls.append("retry" if "previous answer dropped" in sysm else "first")
        return chat_ok("अनुवाद" if "retry" not in calls[-1:] else "अनुवाद ⟦P0⟧")
    c = mock_client(h)
    r = run(t.run("translate", "Use `x=1` now", "en", "hi", client=c))
    assert calls == ["first", "retry"] and "`x=1`" in r.text and r.provider == "ibm-watsonx-ai"

def test_translate_appends_when_still_missing_and_warns():
    def h(req): return iam_ok() if "identity" in str(req.url) else chat_ok("अनुवाद")
    r = run(t.run("translate", "Use `x=1` now", "en", "hi", client=mock_client(h)))
    assert "`x=1`" in r.text and any("dropped" in w for w in r.warnings)

def test_same_language_rejected():
    with pytest.raises(LLMError): run(t.run("translate", "hi", "en", "en", client=mock_client(lambda r: iam_ok())))

def test_demo_mode_is_labelled_not_granite():
    c = GraniteClient(Config())
    r = run(t.run("translate", "Hello world", "en", "hi", client=c))
    assert r.provider == "demo" and "DEMO MODE" in r.text and r.model is None

def test_glossary_parsing():
    j = '```json\n[{"term":"inertia","translation":"जड़त्व","definition":"d"},{"term":"ghost","translation":"x","definition":"d"}]\n```'
    def h(req): return iam_ok() if "identity" in str(req.url) else chat_ok(j)
    g = run(t.glossary("Inertia is a property", "hi", client=mock_client(h)))
    assert [x["in_source"] for x in g["terms"]] == [True, False] and any("verify" in w for w in g["warnings"])

def test_parse_json_variants():
    assert llm.parse_json('Sure!\n{"a":1}') == {"a": 1} and llm.parse_json("```json\n[1,2]\n```") == [1, 2]
    with pytest.raises(LLMError): llm.parse_json("no json here")
