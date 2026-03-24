---
name: foundry-diagnose
description: Slash command for telemetry-backed diagnosis of Azure AI Foundry hosted-agent failures through the Pocket Foundry Operator.
user-invocable: true
metadata: {"openclaw":{"homepage":"https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/orchestrations/human-in-the-loop"}}
---

# Foundry Diagnose

Use this skill for `/foundry_diagnose` requests and any equivalent natural-language request asking why a hosted agent is failing, slow, or unhealthy.

## Intent

This is read-only. Do not ask for approval.

## Argument handling

- If the agent name is missing, ask one concise follow-up question for the target agent.
- Default time window is the last 1 hour unless the user specifies another window.
- If the user asks for traces, exceptions, or logs, include telemetry in the request.
- When the user supplies an agent name, preserve it exactly. Do not substitute a different agent from prior context or earlier results.

## Dispatch contract

Normalize the request into:

```json
{
  "operation": "diagnose",
  "target": {
    "type": "agent",
    "name": "<required agent name>"
  },
  "request": {
    "summary": "Diagnose the target hosted agent and identify probable root cause.",
    "args": {
      "timeWindow": "1h",
      "includeTelemetry": true,
      "correlateExceptions": true,
      "suggestNextStep": true
    },
    "approvalState": "not-required"
  }
}
```

Route the request to `fo-pocket-operator`, which should call `fo-observability` and optionally `fo-smoke`.

Dispatch the normalized JSON to `http://172.18.0.1:8788/dispatch` using the exec tool and `/usr/bin/curl` on host `gateway`.

Use a single `/usr/bin/curl` command with inline `--data` JSON. Do not create temp files. After the curl result returns, do not call any other tool. Summarize the result directly in this chat.

## Output shape

- First line: probable cause in one sentence.
- Then list:
  - evidence
  - confidence
  - immediate next step

Do not dump raw telemetry unless the user explicitly asks for it.

## If the bridge call fails

Report the bridge failure and print the exact payload that was attempted.

