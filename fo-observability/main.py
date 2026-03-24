import json
import logging
import os
import re
import urllib.error
import urllib.request
from collections.abc import AsyncIterable
from urllib.parse import parse_qsl

from dotenv import load_dotenv

load_dotenv(override=False)

AGENT_NAME = "fo-observability"
API_VERSION = "2025-05-15-preview"
PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
APP_INSIGHTS_APP_ID = os.getenv("APP_INSIGHTS_APP_ID", "")

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

logger = logging.getLogger("fo_observability")
logger.setLevel(logging.INFO)

from agent_framework import AgentRunResponse, AgentRunResponseUpdate, BaseAgent, ChatMessage, Role, TextContent
from azure.ai.agentserver.agentframework import from_agent_framework


def _get_bearer_token(scope: str) -> str:
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    return credential.get_token(scope).token


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
    with urllib.request.urlopen(req, timeout=120) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else {}


def _query_app_insights(app_id: str, kql: str) -> list[dict]:
    req = urllib.request.Request(
        f"https://api.applicationinsights.io/v1/apps/{app_id}/query",
        data=json.dumps({"query": kql}).encode(),
        method="POST",
        headers={
            "Authorization": f"Bearer {_get_bearer_token('https://api.applicationinsights.io/.default')}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=120) as resp:
        data = json.loads(resp.read().decode())
    tables = data.get("tables", [])
    if not tables:
        return []
    columns = [col["name"] for col in tables[0].get("columns", [])]
    return [dict(zip(columns, row)) for row in tables[0].get("rows", [])]


def _resolve_app_id() -> str:
    if APP_INSIGHTS_APP_ID:
        return APP_INSIGHTS_APP_ID

    connections = _request_json(
        "GET",
        f"{PROJECT_ENDPOINT}/connections?api-version={API_VERSION}&category=AppInsights",
        scope="https://ai.azure.com/.default",
    )
    items = connections.get("value", connections.get("data", []))
    if isinstance(connections, list):
        items = connections
    if not items:
        return ""
    resource_id = items[0].get("properties", {}).get("target", "")
    if not resource_id:
        return ""
    component = _request_json(
        "GET",
        f"https://management.azure.com{resource_id}?api-version=2015-05-01",
        scope="https://management.azure.com/.default",
    )
    app_id = component.get("properties", {}).get("AppId", "")
    if app_id:
        return app_id

    try:
        from azure.identity import DefaultAzureCredential
        from azure.ai.projects import AIProjectClient

        credential = DefaultAzureCredential()
        client = AIProjectClient(credential=credential, endpoint=PROJECT_ENDPOINT)
        conn_str = client.telemetry.get_application_insights_connection_string() or ""
        if conn_str:
            parts = dict(parse_qsl(conn_str.replace(";", "&")))
            app_id = parts.get("ApplicationId", "")
            if app_id:
                return app_id
    except Exception:
        pass

    return ""


def _safe_agent_name(agent_name: str) -> str:
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9-]{0,62}$", agent_name):
        raise ValueError(f"Unsafe agent name: {agent_name!r}")
    return agent_name


def _build_kql(agent_name: str, since: str, limit: int) -> str:
    safe_name = _safe_agent_name(agent_name)
    return (
        f"union isfuzzy=true exceptions, requests, traces"
        f" | where timestamp > ago({since})"
        f" | where message !contains 'readiness' and message !contains 'liveness'"
        f" | where * contains '{safe_name}'"
        f" | order by timestamp desc"
        f" | take {limit}"
        f" | project timestamp, itemType, message, name, resultCode, type, outerMessage, innermostMessage, severityLevel"
    )


def _normalize_input(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"target": {"type": "agent", "name": ""}, "request": {"args": {"timeWindow": "1h"}}}


def _diagnose(request: dict) -> dict:
    target_name = (request.get("target") or {}).get("name", "")
    if not target_name:
        return {"status": "error", "summary": "Missing target agent name.", "details": []}

    time_window = request.get("request", {}).get("args", {}).get("timeWindow", "1h")
    app_id = _resolve_app_id()
    if not app_id:
        return {"status": "error", "summary": "Could not resolve Application Insights App ID.", "details": []}

    try:
        rows = _query_app_insights(app_id, _build_kql(target_name, time_window, 10))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode() if exc.fp else ""
        return {"status": "error", "summary": f"App Insights query failed with HTTP {exc.code}.", "details": [body[:300]]}

    if not rows:
        return {
            "status": "ok",
            "summary": f"No recent telemetry found for {target_name} in the last {time_window}.",
            "details": ["Run a smoke test to verify the agent is still reachable."],
        }

    evidence: list[str] = []
    exception_count = 0
    request_failures = 0
    for row in rows[:5]:
        item_type = row.get("itemType", "other")
        if item_type == "exception":
            exception_count += 1
        if item_type == "request" and str(row.get("resultCode", "")) not in ("200", "201", "202", ""):
            request_failures += 1
        message = row.get("outerMessage") or row.get("innermostMessage") or row.get("message") or row.get("name") or ""
        evidence.append(f"{item_type}: {str(message)[:180]}")

    if exception_count:
        summary = f"{target_name}: probable runtime exception pattern detected"
    elif request_failures:
        summary = f"{target_name}: recent request failures detected"
    else:
        summary = f"{target_name}: telemetry shows recent activity without obvious hard failures"

    details = evidence + ["Use a smoke test to confirm current behavior before changing versions."]
    return {"status": "ok", "summary": summary, "details": details}


class ObservabilityAgent(BaseAgent):
    async def run(self, messages=None, *, thread=None, **kwargs) -> AgentRunResponse:
        normalized = self._normalize_messages(messages)
        user_input = normalized[-1].text if normalized else "{}"
        result = _diagnose(_normalize_input(user_input))
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
    return ObservabilityAgent(name=AGENT_NAME, description="Observability specialist for hosted agents")


if __name__ == "__main__":
    logger.info("[%s] starting up", AGENT_NAME)
    from_agent_framework(create_agent()).run()
