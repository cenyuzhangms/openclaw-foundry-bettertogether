---
name: foundry-operator-core
description: Shared operating rules for Pocket Foundry Operator requests coming from OpenClaw into Azure AI Foundry Hosted Agents and Microsoft Agent Framework workflows.
user-invocable: false
metadata: {"openclaw":{"homepage":"https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview"}}
---

# Foundry Operator Core

Use this skill when the user wants to operate, inspect, diagnose, or change Azure AI Foundry hosted agents from an OpenClaw chat surface.

## Role split

- OpenClaw is the chat ingress, identity boundary, and approval surface.
- `fo-pocket-operator` is the single runtime entrypoint in Foundry.
- `fo-inventory-health` handles inventory and health summaries.
- `fo-observability` handles telemetry and root-cause diagnosis.
- `fo-smoke` runs post-change or on-demand smoke tests.
- `fo-change-controller` performs approved mutations only.
- Microsoft Agent Framework is the orchestration layer inside the hosted agents.

Do not move orchestration logic into OpenClaw. OpenClaw should package requests cleanly, preserve channel context, and require approval for mutation.

## Safety rules

- Treat `status`, `inventory`, `diagnose`, and `smoke` as read-only operations.
- Treat `restart`, `stop`, `scale`, `redeploy`, `rotate`, `enable`, and `disable` as mutation operations.
- Never execute a mutation on first request. First produce a plan with impact, rollback, and verification.
- If the user asks for a risky action, require explicit approval text before dispatching the mutation request.
- Prefer one short clarification question only when the target agent or action is ambiguous.

## Runtime contract

This OpenClaw VM has a Foundry bridge running for these skills. Package requests into this shape and send them to `fo-pocket-operator`:

```json
{
  "operation": "status|diagnose|smoke|change",
  "channel": {
    "platform": "discord|teams|control-ui",
    "chatType": "dm|group|channel",
    "threadId": "<channel thread identifier>",
    "userId": "<sender id>"
  },
  "project": {
    "endpoint": "<AZURE_AI_PROJECT_ENDPOINT or project alias>",
    "environment": "dev|test|prod"
  },
  "target": {
    "type": "project|agent",
    "name": "<optional agent name>"
  },
  "request": {
    "summary": "<normalized user intent>",
    "args": {},
    "approvalState": "not-required|plan-only|approved"
  }
}
```

Recommended bridge contract for this workspace:

- service: `foundry-pocket-operator-demo/openclaw-bridge`
- endpoint: `POST /dispatch`
- auth: `X-OpenClaw-Secret` header when a shared secret is configured

## Bridge execution on the gateway

Call the bridge from the gateway host with `curl` instead of fabricating an answer.

Preferred command shape:

```bash
curl -sS -X POST http://172.18.0.1:8788/dispatch \
  -H 'Content-Type: application/json' \
  --data '<normalized request json>'
```

`/usr/bin/curl` is already allowlisted for agent `main` on this gateway. Use it directly.

Execution rules:

- Use exactly one exec call for the bridge dispatch, then stop using tools.
- Prefer a single `/usr/bin/curl` command with inline `--data` JSON.
- Do not create temp files and do not use `cat`, `python`, `sh`, or follow-up shell commands unless the bridge call itself fails.
- After the bridge returns JSON, answer directly in the current chat session.
- Do not call any send-message, channel-routing, or cross-channel tool.
- Do not ask the user to choose a channel. Stay in the current conversation.

Expected response shape:

```json
{
  "status": "ok|needs-approval|error",
  "summary": "<short mobile-friendly answer>",
  "details": ["<evidence or findings>"],
  "approval": {
    "required": true,
    "action": "<proposed mutation>",
    "impact": "<expected effect>",
    "rollback": "<rollback plan>",
    "verify": "<verification step>"
  }
}
```

## If the bridge call fails

Only if the bridge call itself returns a connection or HTTP error should you fall back to reporting that the bridge is unavailable.

When that happens:

1. State that the OpenClaw-to-Foundry bridge call failed.
2. Show the normalized JSON payload that was attempted.
3. Include the concrete error from the failed bridge call.

## Response style

- Keep the first line short and operator-friendly.
- Prefer direct findings, then next action.
- In mobile/chat contexts, keep verbose telemetry dumps out of the first reply.
- If you have evidence, cite it as bullets, not a wall of prose.

