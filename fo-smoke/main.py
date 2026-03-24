import json
import logging
import os
import urllib.error
import urllib.request
from collections.abc import AsyncIterable

from dotenv import load_dotenv

load_dotenv(override=False)

AGENT_NAME = "fo-smoke"
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

logger = logging.getLogger("fo_smoke")
logger.setLevel(logging.INFO)

from agent_framework import AgentRunResponse, AgentRunResponseUpdate, BaseAgent, ChatMessage, Role, TextContent
from azure.ai.agentserver.agentframework import from_agent_framework


def _get_bearer_token() -> str:
    from azure.identity import DefaultAzureCredential

    credential = DefaultAzureCredential()
    return credential.get_token("https://ai.azure.com/.default").token


def _extract_output_text(result: dict) -> str:
    chunks: list[str] = []
    for item in result.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") == "output_text":
                chunks.append(content.get("text", ""))
    return "".join(chunks).strip()


def _invoke_agent(agent_name: str, prompt: str) -> tuple[bool, str]:
    body = {
        "input": prompt,
        "agent": {"name": agent_name, "type": "agent_reference"},
        "store": False,
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
        with urllib.request.urlopen(req, timeout=120) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode() if exc.fp else ""
        return False, f"HTTP {exc.code}: {body_text[:240] or exc.reason}"
    return True, _extract_output_text(result)


def _normalize_input(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"target": {"type": "agent", "name": ""}, "request": {"args": {}}}


def _run_smoke(request: dict) -> dict:
    target_name = (request.get("target") or {}).get("name", "")
    if not target_name:
        return {"status": "error", "summary": "Missing target agent name.", "details": []}

    args = request.get("request", {}).get("args", {})
    prompt = args.get(
        "smokePrompt",
        "Reply with READY, then one sentence describing what you can do.",
    )
    ok, output = _invoke_agent(target_name, prompt)
    if not ok:
        return {"status": "error", "summary": f"{target_name}: smoke test failed", "details": [output]}

    return {
        "status": "ok",
        "summary": f"{target_name}: smoke test passed",
        "details": [f"prompt={prompt}", f"response={output[:240]}"],
    }


class SmokeAgent(BaseAgent):
    async def run(self, messages=None, *, thread=None, **kwargs) -> AgentRunResponse:
        normalized = self._normalize_messages(messages)
        user_input = normalized[-1].text if normalized else "{}"
        result = _run_smoke(_normalize_input(user_input))
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
    return SmokeAgent(name=AGENT_NAME, description="Smoke-test specialist for hosted agents")


if __name__ == "__main__":
    logger.info("[%s] starting up", AGENT_NAME)
    from_agent_framework(create_agent()).run()
