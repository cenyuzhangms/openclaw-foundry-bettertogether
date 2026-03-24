import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

load_dotenv(override=False)

PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
OPERATOR_AGENT_NAME = os.getenv("FOUNDRY_OPERATOR_AGENT_NAME", "fo-pocket-operator")
BRIDGE_SECRET = os.getenv("OPENCLAW_BRIDGE_SHARED_SECRET", "")
USE_CONVERSATIONS = os.getenv("OPENCLAW_BRIDGE_USE_CONVERSATIONS", "false").lower() in ("1", "true", "yes", "on")
API_VERSION = "2025-05-15-preview"
STATE_PATH = Path(__file__).with_name(".bridge-state.json")

if not PROJECT_ENDPOINT:
    raise RuntimeError("AZURE_AI_PROJECT_ENDPOINT must be set")


class OpenClawRequest(BaseModel):
    operation: str
    channel: dict = {}
    project: dict = {}
    target: dict = {}
    request: dict = {}


def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")


def _get_bearer_token(scope: str) -> str:
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    token = credential.get_token(scope)
    return token.token


def _request_json(method: str, url: str, *, body: dict | None = None, scope: str) -> dict:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {_get_bearer_token(scope)}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode() if e.fp else ""
        raise HTTPException(status_code=502, detail=f"Foundry bridge error {e.code}: {body_text[:300]}")
    if not raw.strip():
        return {}
    return json.loads(raw)


def _extract_output_text(result: dict) -> str:
    output_parts: list[str] = []
    for item in result.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                output_parts.append(content.get("text", ""))
    return "".join(output_parts).strip()


def _thread_key(payload: OpenClawRequest) -> str:
    channel = payload.channel or {}
    platform = channel.get("platform", "unknown")
    chat_type = channel.get("chatType", "unknown")
    thread_id = channel.get("threadId") or channel.get("userId") or "anonymous"
    return f"{platform}:{chat_type}:{thread_id}"


def _get_or_create_conversation(thread_key: str) -> str:
    state = _load_state()
    conversation_id = state.get("conversations", {}).get(thread_key, "")
    if conversation_id:
        return conversation_id
    created = _request_json(
        "POST",
        f"{PROJECT_ENDPOINT}/openai/conversations?api-version={API_VERSION}",
        body={},
        scope="https://ai.azure.com/.default",
    )
    conversation_id = created.get("id", "")
    if not conversation_id:
        return ""
    conversations = state.setdefault("conversations", {})
    conversations[thread_key] = conversation_id
    _save_state(state)
    return conversation_id


app = FastAPI(title="OpenClaw Foundry Bridge")


@app.get("/health")
def health() -> dict:
    return {"ok": True, "operator": OPERATOR_AGENT_NAME, "useConversations": USE_CONVERSATIONS}


@app.post("/dispatch")
def dispatch(payload: OpenClawRequest, x_openclaw_secret: str | None = Header(default=None)) -> dict:
    if BRIDGE_SECRET and x_openclaw_secret != BRIDGE_SECRET:
        raise HTTPException(status_code=401, detail="Invalid shared secret")

    conversation_id = _get_or_create_conversation(_thread_key(payload)) if USE_CONVERSATIONS else ""
    body = {
        "input": json.dumps(payload.model_dump(), indent=2),
        "agent": {"name": OPERATOR_AGENT_NAME, "type": "agent_reference"},
        "store": True,
    }
    if conversation_id:
        body["conversation"] = {"id": conversation_id}

    result = _request_json(
        "POST",
        f"{PROJECT_ENDPOINT}/openai/responses?api-version={API_VERSION}",
        body=body,
        scope="https://ai.azure.com/.default",
    )
    text = _extract_output_text(result)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = {"status": "ok", "summary": text, "details": []}

    return {
        "conversationId": conversation_id,
        "operatorAgent": OPERATOR_AGENT_NAME,
        "result": parsed,
        "rawText": text,
    }
