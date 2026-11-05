"""IBM watsonx.ai (Granite) client for Bhasha Bridge.

Auth:     IBM Cloud API key -> IAM bearer token (cached until shortly before expiry)
Endpoint: POST {WATSONX_URL}/ml/v1/text/chat?version={WATSONX_API_VERSION}
Secrets:  read from environment only; never sent to the browser, never logged.

If credentials are missing the client runs in DEMO mode. Demo output is always
labelled (provider == "demo") and is NEVER presented as IBM Granite output.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import dataclass, field

import httpx

IAM_URL = os.environ.get("IBM_IAM_URL", "https://iam.cloud.ibm.com/identity/token")
DEFAULT_API_VERSION = "2024-05-31"


class LLMError(Exception):
    """User-presentable AI provider error."""

    def __init__(self, message: str, status: int = 502, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.status = status
        self.retryable = retryable


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    requests: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class Config:
    api_key: str = ""
    project_id: str = ""
    base_url: str = "https://us-south.ml.cloud.ibm.com"
    model_id: str = "ibm/granite-4-h-small"
    api_version: str = DEFAULT_API_VERSION
    force_demo: bool = False
    timeout: float = 120.0

    @classmethod
    def from_env(cls) -> "Config":
        e = os.environ.get
        return cls(
            api_key=(e("WATSONX_API_KEY") or "").strip(),
            project_id=(e("WATSONX_PROJECT_ID") or "").strip(),
            base_url=(e("WATSONX_URL") or "https://us-south.ml.cloud.ibm.com").strip().rstrip("/"),
            model_id=(e("WATSONX_MODEL_ID") or "ibm/granite-4-h-small").strip(),
            api_version=(e("WATSONX_API_VERSION") or DEFAULT_API_VERSION).strip(),
            force_demo=(e("DEMO_MODE", "").lower() in ("1", "true", "yes")),
        )

    @property
    def is_demo(self) -> bool:
        return self.force_demo or not (self.api_key and self.project_id)


class GraniteClient:
    def __init__(self, config: Config | None = None, transport: httpx.AsyncBaseTransport | None = None):
        self.config = config or Config.from_env()
        self._transport = transport  # injectable for tests
        self._token = ""
        self._token_exp = 0.0
        self._lock = asyncio.Lock()
        self.usage = Usage()
        self._gate = asyncio.Semaphore(2)  # Lite plan: 2 inference requests / second

    # ── status (safe to expose; contains no secrets) ────────────────────────
    def status(self) -> dict:
        c = self.config
        if c.is_demo:
            why = ("DEMO_MODE is enabled" if c.force_demo
                   else "WATSONX_API_KEY / WATSONX_PROJECT_ID are not set")
            return {"mode": "demo", "provider": "demo", "model": None,
                    "message": f"DEMO MODE: {why}. Output is placeholder text, NOT from IBM Granite."}
        return {"mode": "granite", "provider": "ibm-watsonx-ai", "model": c.model_id,
                "region_url": c.base_url, "message": "Connected to IBM watsonx.ai",
                "session_tokens_used": self.usage.total, "session_requests": self.usage.requests}

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=self.config.timeout, transport=self._transport)

    # ── auth ────────────────────────────────────────────────────────────────
    async def _bearer(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._token and time.time() < self._token_exp - 60:
                return self._token
            try:
                async with self._client() as c:
                    r = await c.post(
                        IAM_URL,
                        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
                        data={"grant_type": "urn:ibm:params:oauth:grant-type:apikey", "apikey": self.config.api_key},
                    )
            except httpx.HTTPError as e:
                raise LLMError(f"Could not reach IBM Cloud authentication ({type(e).__name__}). Check your network.", 503, True)
            if r.status_code in (400, 401, 403):
                raise LLMError("IBM Cloud rejected the API key. Check WATSONX_API_KEY (create a new key in IBM Cloud > Manage > Access (IAM) > API keys).", 401)
            if r.status_code != 200:
                raise LLMError(f"IBM Cloud authentication failed (HTTP {r.status_code}).", 502, r.status_code >= 500)
            data = r.json()
            self._token = data["access_token"]
            self._token_exp = time.time() + float(data.get("expires_in", 3600))
            return self._token

    # ── inference ───────────────────────────────────────────────────────────
    async def chat(self, system: str, messages: list, max_tokens: int = 1500, temperature: float = 0.2) -> str:
        """Single chat completion. Returns text. Raises LLMError."""
        if self.config.is_demo:
            raise LLMError("AI is in demo mode (no IBM credentials).", 503)
        c = self.config
        body = {
            "model_id": c.model_id,
            "project_id": c.project_id,
            "messages": ([{"role": "system", "content": system}] if system else [])
                        + [{"role": m["role"], "content": m["content"]} for m in messages],
            "max_tokens": int(max_tokens),
            "temperature": temperature,
        }
        url = f"{c.base_url}/ml/v1/text/chat?version={c.api_version}"
        last: LLMError | None = None
        refreshed = False
        for attempt in range(4):
            token = await self._bearer()
            try:
                async with self._gate:
                    async with self._client() as client:
                        r = await client.post(url, json=body, headers={
                            "Authorization": f"Bearer {token}", "Content-Type": "application/json",
                            "Accept": "application/json"})
            except httpx.TimeoutException:
                last = LLMError("IBM watsonx.ai timed out. Try a shorter section.", 504, True)
            except httpx.HTTPError as e:
                last = LLMError(f"Could not reach IBM watsonx.ai ({type(e).__name__}).", 503, True)
            else:
                if r.status_code == 200:
                    return self._parse(r.json())
                last = self._map_error(r)
                if r.status_code == 401 and not refreshed:  # expired token: refresh once
                    refreshed = True
                    await self._bearer(force=True)
                    continue
                if last.retryable:
                    ra = r.headers.get("retry-after")
                    await asyncio.sleep(min(float(ra) if ra and ra.replace(".", "").isdigit() else 2 ** attempt, 20))
                    continue
                raise last
            await asyncio.sleep(2 ** attempt)
        raise last or LLMError("Unknown AI error.")

    def _parse(self, data: dict) -> str:
        try:
            text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMError("IBM watsonx.ai returned an unexpected response format.", 502)
        u = data.get("usage") or {}
        self.usage.prompt_tokens += int(u.get("prompt_tokens", 0) or 0)
        self.usage.completion_tokens += int(u.get("completion_tokens", 0) or 0)
        self.usage.requests += 1
        return (text or "").strip()

    @staticmethod
    def _map_error(r: httpx.Response) -> LLMError:
        try:
            j = r.json()
            detail = (j.get("errors") or [{}])[0].get("message") or j.get("message") or j.get("error") or ""
        except Exception:  # noqa: BLE001
            detail = ""
        s = r.status_code
        if s == 401:
            return LLMError("IBM watsonx.ai rejected the access token (401).", 401)
        if s == 403:
            return LLMError("Access denied by watsonx.ai (403). Check that WATSONX_PROJECT_ID is correct and that the project has a watsonx.ai Runtime service associated.", 403)
        if s == 404:
            return LLMError("watsonx.ai endpoint or model not found (404). Check WATSONX_URL (region) and WATSONX_MODEL_ID.", 404)
        if s == 429:
            return LLMError("IBM watsonx.ai rate limit or monthly Lite token quota reached (429). Wait a moment, or check your usage in IBM Cloud.", 429, True)
        if s in (400, 422):
            return LLMError(f"watsonx.ai rejected the request ({s}): {detail[:200]}", 400)
        return LLMError(f"IBM watsonx.ai error (HTTP {s}). {detail[:200]}", 502, s >= 500)


# ── helpers ──────────────────────────────────────────────────────────────────
def parse_json(text: str):
    """Parse a JSON object/array out of an LLM reply (tolerates ``` fences and chatter)."""
    t = re.sub(r"```(?:json)?", "", text or "").strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = t.find(open_c), t.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except json.JSONDecodeError:
                continue
    raise LLMError("The AI reply was not valid JSON. Please try again.", 502, True)


_client: GraniteClient | None = None


def get_client() -> GraniteClient:
    global _client
    if _client is None:
        _client = GraniteClient()
    return _client


def reset_client(client: GraniteClient | None = None) -> None:
    global _client
    _client = client
