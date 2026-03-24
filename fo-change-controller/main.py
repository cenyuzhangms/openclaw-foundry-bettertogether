import json
import logging
import os
import urllib.request
from collections.abc import AsyncIterable

from dotenv import load_dotenv

load_dotenv(override=False)

AGENT_NAME = "fo-change-controller"
API_VERSION = "2025-05-15-preview"
PROJECT_ENDPOINT = os.getenv("AZURE_AI_PROJECT_ENDPOINT")
MODEL_DEPLOYMENT_NAME = os.getenv("MODEL_DEPLOYMENT_NAME", "gpt-4.1-mini")

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

logger = logging.getLogger("fo_change_controller")
logger.setLevel(logging.INFO)

from agent_framework import (
    AgentRunEvent,
    AgentRunResponse,
    AgentRunResponseUpdate,
    BaseAgent,
    ChatMessage,
    Role,
    TextContent,
    WorkflowBuilder,
)
from agent_framework.azure import AzureOpenAIChatClient
from azure.ai.agentserver.agentframework import from_agent_framework
from azure.identity import DefaultAzureCredential, get_bearer_token_provider
from urllib.parse import urlparse as _urlparse

_parsed = _urlparse(PROJECT_ENDPOINT)
OPENAI_ENDPOINT = f"{_parsed.scheme}://{_parsed.netloc}"


def _get_bearer_token() -> str:
    credential = DefaultAzureCredential()
    return credential.get_token("https://ai.azure.com/.default").token


def _request_json(method: str, path: str, *, body: dict | None = None) -> dict:
    payload = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{PROJECT_ENDPOINT}{path}",
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {_get_bearer_token()}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=180) as resp:
        raw = resp.read().decode()
    return json.loads(raw) if raw.strip() else {}


def _normalize_input(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"target": {"type": "agent", "name": ""}, "request": {"args": {}, "approvalState": "plan-only"}}


def _get_agent_record(agent_name: str) -> dict:
    return _request_json("GET", f"/agents/{agent_name}?api-version={API_VERSION}")


def _create_redeploy_version(agent_name: str) -> dict:
    agent = _get_agent_record(agent_name)
    latest = agent.get("versions", {}).get("latest", {})
    definition = latest.get("definition", {})
    if not definition:
        return {"status": "error", "summary": f"No latest definition found for {agent_name}.", "details": []}
    created = _request_json(
        "POST",
        f"/agents/{agent_name}/versions?api-version={API_VERSION}",
        body={"definition": definition, "metadata": {"enableVnextExperience": "true"}},
    )
    version = created.get("version", created.get("name", "unknown"))
    status = created.get("status", "creating")
    return {
        "status": "ok",
        "summary": f"{agent_name}: redeploy started as version {version}",
        "details": [f"newVersion={version}", f"status={status}", "Run a smoke test after the version becomes active."],
    }


class ChangeControllerAgent(BaseAgent):
    def _build_planning_workflow(self):
        credential = DefaultAzureCredential()
        token_provider = get_bearer_token_provider(
            credential, "https://cognitiveservices.azure.com/.default"
        )
        client = AzureOpenAIChatClient(
            endpoint=OPENAI_ENDPOINT,
            deployment_name=MODEL_DEPLOYMENT_NAME,
            ad_token_provider=token_provider,
        )
        planner = client.create_agent(
            name="ChangePlanner",
            instructions=(
                "Produce a concise change plan for a Foundry hosted agent operation. "
                "Include expected impact, rollback, and verification."
            ),
        )
        reviewer = client.create_agent(
            name="RiskReviewer",
            instructions=(
                "Review a hosted-agent change plan for operator safety. "
                "Highlight missing rollback or verification details in concise bullets."
            ),
        )
        return WorkflowBuilder().set_start_executor(planner).add_edge(planner, reviewer).build()

    async def _plan_change(self, request: dict) -> dict:
        target_name = (request.get("target") or {}).get("name", "")
        args = request.get("request", {}).get("args", {})
        action = args.get("action", "")
        if not target_name or not action:
            return {"status": "error", "summary": "Missing target agent or action for change planning.", "details": []}

        if action != "redeploy":
            return {
                "status": "error",
                "summary": f"Action '{action}' is not executable in this demo runtime.",
                "details": ["Supported executed change: redeploy", "Other actions can still be planned but not run."],
            }

        agent = _get_agent_record(target_name)
        latest = agent.get("versions", {}).get("latest", {})
        version = latest.get("version", "unknown")
        image = latest.get("definition", {}).get("image", "unknown")
        prompt = (
            f"Target agent: {target_name}\n"
            f"Requested action: {action}\n"
            f"Current version: {version}\n"
            f"Current image: {image}\n"
            "Return concise operator-ready bullets only."
        )

        workflow = self._build_planning_workflow()
        result = await workflow.run(prompt)
        plan_lines: list[str] = []
        for event in result:
            if isinstance(event, AgentRunEvent) and getattr(event, "data", None) is not None:
                text = getattr(event.data, "text", "")
                if text:
                    plan_lines.append(text.strip())

        details = plan_lines or ["Planner did not return detail text."]
        return {
            "status": "needs-approval",
            "summary": f"{target_name}: redeploy plan ready for approval",
            "details": details,
            "approval": {
                "required": True,
                "action": "redeploy",
                "impact": f"Creates a new version from the current definition for {target_name}.",
                "rollback": "If the new version is unhealthy, redeploy the previous known-good definition as the next version.",
                "verify": f"Run a smoke test against {target_name} after the new version becomes active.",
            },
        }

    async def run(self, messages=None, *, thread=None, **kwargs) -> AgentRunResponse:
        normalized = self._normalize_messages(messages)
        user_input = normalized[-1].text if normalized else "{}"
        request = _normalize_input(user_input)
        args = request.get("request", {}).get("args", {})
        approval_state = request.get("request", {}).get("approvalState", "plan-only")
        action = args.get("action", "")

        if approval_state == "approved" and action == "redeploy":
            result = _create_redeploy_version((request.get("target") or {}).get("name", ""))
        else:
            result = await self._plan_change(request)

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
    return ChangeControllerAgent(name=AGENT_NAME, description="Change planning and redeploy execution")


if __name__ == "__main__":
    logger.info("[%s] starting up", AGENT_NAME)
    from_agent_framework(create_agent()).run()
