import asyncio
import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import AsyncIterable

from dotenv import load_dotenv

load_dotenv(override=False)

AGENT_NAME = "fo-pocket-operator"
API_VERSION = "2025-05-15-preview"
PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")

if not PROJECT_ENDPOINT:
    raise ValueError("AZURE_AI_PROJECT_ENDPOINT environment variable must be set")

os.environ["ENABLE_APPLICATION_INSIGHTS_LOGGER"] = "false"


def _setup_telemetry() -> None:
    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient
        from azure.monitor.opentelemetry import configure_azure_monitor

        credential = DefaultAzureCredential()
        client = AIProjectClient(credential=credential, endpoint=PROJECT_ENDPOINT)
        conn_str = client.telemetry.get_application_insights_connection_string()
        if conn_str:
            os.environ["APPLICATIONINSIGHTS_CONNECTION_STRING"] = conn_str
            configure_azure_monitor(
                credential=credential,
                logger_name="",
                logging_level=logging.INFO,
            )
    except Exception as exc:
        print(f"Telemetry setup skipped: {exc}")


_setup_telemetry()

logger = logging.getLogger("fo_pocket_operator")
logger.setLevel(logging.INFO)

from agent_framework import AgentRunResponse, AgentRunResponseUpdate, BaseAgent, ChatMessage, Role, TextContent
from azure.ai.agentserver.agentframework import from_agent_framework


def _get_bearer_token() -> str:
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    token = credential.get_token("https://ai.azure.com/.default")
    return token.token


def _invoke_agent(agent_name: str, payload: dict) -> dict:
    body = {
        "input": json.dumps(payload, indent=2),
        "agent": {"name": agent_name, "type": "agent_reference"},
        "store": True,
    }
    req = urllib.request.Request(
        f"{PROJECT_ENDPOINT}/openai/responses?api-version={API_VERSION}",
        data=json.dumps(body).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {_get_bearer_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode() if exc.fp else ""
        return {
            "status": "error",
            "summary": f"{agent_name} returned HTTP {exc.code}",
            "details": [body_text[:300] or exc.reason],
        }

    chunks: list[str] = []
    for item in result.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    text = "".join(chunks).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"status": "ok", "summary": text, "details": []}


def _normalize_request(text: str) -> dict:
    try:
        request = json.loads(text)
        if isinstance(request, dict):
            return request
    except json.JSONDecodeError:
        pass
    return {
        "operation": "status",
        "target": {"type": "project"},
        "request": {"summary": text, "approvalState": "not-required", "args": {}},
    }


def _merge_read_results(primary: dict, secondary: dict | None = None) -> dict:
    merged = {
        "status": primary.get("status", "ok"),
        "summary": primary.get("summary", ""),
        "details": list(primary.get("details", [])),
    }
    if secondary:
        if secondary.get("summary"):
            merged["details"].append(secondary["summary"])
        merged["details"].extend(secondary.get("details", []))
        if secondary.get("status") == "error":
            merged["status"] = "error"
    return merged


class PocketOperatorAgent(BaseAgent):
    async def run(self, messages=None, *, thread=None, **kwargs) -> AgentRunResponse:
        normalized = self._normalize_messages(messages)
        user_input = normalized[-1].text if normalized else "{}"
        request = _normalize_request(user_input)
        operation = request.get("operation", "status")

        logger.info("[%s] dispatch operation=%s", AGENT_NAME, operation)

        if operation == "status":
            result = await asyncio.to_thread(_invoke_agent, "fo-inventory-health", request)
        elif operation == "diagnose":
            obs = await asyncio.to_thread(_invoke_agent, "fo-observability", request)
            smoke = None
            if request.get("request", {}).get("args", {}).get("runSmokeAfterDiagnosis"):
                smoke = await asyncio.to_thread(_invoke_agent, "fo-smoke", request)
            result = _merge_read_results(obs, smoke)
        elif operation == "smoke":
            result = await asyncio.to_thread(_invoke_agent, "fo-smoke", request)
        elif operation == "change":
            result = await asyncio.to_thread(_invoke_agent, "fo-change-controller", request)
        else:
            result = {
                "status": "error",
                "summary": f"Unsupported operation '{operation}'",
                "details": ["Use status, diagnose, smoke, or change."],
            }

        text = json.dumps(result, indent=2)
        msg = ChatMessage(role=Role.ASSISTANT, contents=[TextContent(text=text)])
        if thread is not None:
            await self._notify_thread_of_new_messages(thread, normalized, msg)
        return AgentRunResponse(messages=[msg])

    async def run_stream(self, messages=None, *, thread=None, **kwargs) -> AsyncIterable[AgentRunResponseUpdate]:
        response = await self.run(messages=messages, thread=thread, **kwargs)
        for msg in response.messages:
            yield AgentRunResponseUpdate(role=msg.role, contents=msg.contents)


def create_agent():
    return PocketOperatorAgent(
        name=AGENT_NAME,
        description="Pocket Foundry Operator orchestrator",
    )


if __name__ == "__main__":
    from_agent_framework(create_agent()).run()
