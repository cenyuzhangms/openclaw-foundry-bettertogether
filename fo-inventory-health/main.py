import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import AsyncIterable

from dotenv import load_dotenv

load_dotenv(override=False)

AGENT_NAME = "fo-inventory-health"
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
            configure_azure_monitor(credential=credential, logger_name="", logging_level=logging.INFO)
    except Exception as exc:
        print(f"Telemetry setup skipped: {exc}")


_setup_telemetry()

logger = logging.getLogger("fo_inventory_health")
logger.setLevel(logging.INFO)

from agent_framework import AgentRunResponse, AgentRunResponseUpdate, BaseAgent, ChatMessage, Role, TextContent
from azure.ai.agentserver.agentframework import from_agent_framework


def _get_bearer_token() -> str:
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    return credential.get_token("https://ai.azure.com/.default").token


def _request_json(path: str) -> dict:
    req = urllib.request.Request(
        f"{PROJECT_ENDPOINT}{path}",
        method="GET",
        headers={"Authorization": f"Bearer {_get_bearer_token()}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        raise RuntimeError(f"HTTP {exc.code}: {body[:300] or exc.reason}") from exc
    return json.loads(raw) if raw.strip() else {}


def _resolve_version_status(agent_name: str, version: str) -> str:
    if not agent_name or not version or version == "?":
        return "unknown"
    try:
        version_result = _request_json(f"/agents/{agent_name}/versions/{version}?api-version={API_VERSION}")
    except Exception:
        return "unknown"
    return version_result.get("status", "unknown")


def _normalize_input(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"target": {"type": "project"}, "request": {"summary": text}}


def _format_result(request: dict) -> dict:
    target_name = (request.get("target") or {}).get("name", "")
    try:
        result = _request_json(f"/agents?api-version={API_VERSION}")
    except Exception as exc:
        return {
            "status": "error",
            "summary": "Failed to query hosted-agent inventory.",
            "details": [str(exc)],
        }
    agents = result.get("data", result.get("value", []))
    if isinstance(result, list):
        agents = result

    if target_name:
        agents = [agent for agent in agents if agent.get("name") == target_name]

    details: list[str] = []
    active_count = 0
    failed_count = 0

    for agent in agents:
        name = agent.get("name", "?")
        latest = agent.get("versions", {}).get("latest", {})
        version = latest.get("version", "?")
        status = latest.get("status") or _resolve_version_status(name, version)
        if status == "active":
            active_count += 1
        if status == "failed":
            failed_count += 1
        image = latest.get("definition", {}).get("image", "")
        image_note = f" image={image}" if image else ""
        details.append(f"{name}: version={version} status={status}{image_note}")

    if not agents:
        summary = f"No hosted agents matched '{target_name}'." if target_name else "No hosted agents found."
        return {"status": "error", "summary": summary, "details": []}

    scope = target_name or "project"
    summary = f"{scope}: {active_count}/{len(agents)} active"
    if failed_count:
        summary += f"; {failed_count} failed"

    details.append(
        "Run a diagnose request for any failed agent." if failed_count else "No immediate action recommended."
    )
    return {"status": "ok", "summary": summary, "details": details}


class InventoryHealthAgent(BaseAgent):
    async def run(self, messages=None, *, thread=None, **kwargs) -> AgentRunResponse:
        normalized = self._normalize_messages(messages)
        user_input = normalized[-1].text if normalized else "{}"
        result = _format_result(_normalize_input(user_input))
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
    return InventoryHealthAgent(name=AGENT_NAME, description="Inventory and health summaries for hosted agents")


if __name__ == "__main__":
    logger.info("[%s] starting up", AGENT_NAME)
    from_agent_framework(create_agent()).run()
