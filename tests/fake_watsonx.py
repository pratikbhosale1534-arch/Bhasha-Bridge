"""LOCAL FAKE of the IBM IAM + watsonx.ai chat endpoints, for UI/E2E testing ONLY.
It is NOT IBM and proves nothing about IBM compatibility."""
import json, re
from fastapi import FastAPI, Request
app = FastAPI()

@app.post("/identity/token")
async def token(): return {"access_token": "fake-token", "expires_in": 3600}

@app.post("/ml/v1/text/chat")
async def chat(req: Request):
    b = await req.json()
    system = b["messages"][0]["content"]; user = b["messages"][-1]["content"]
    if "study glossary" in system:
        return {"choices": [{"message": {"content": '```json\n[{"term":"inertia","translation":"जड़त्व","definition":"वस्तु की गति न बदलने की प्रवृत्ति।"},{"term":"newton","translation":"न्यूटन","definition":"बल की इकाई।"}]\n```'}}], "usage": {"prompt_tokens": 50, "completion_tokens": 40}}
    tag = "[MOCK-सरल]" if "SIMPLIFICATION" in system else "[MOCK-अनुवाद]"
    return {"choices": [{"message": {"content": f"{tag} {user}"}}], "usage": {"prompt_tokens": 30, "completion_tokens": 30}}
