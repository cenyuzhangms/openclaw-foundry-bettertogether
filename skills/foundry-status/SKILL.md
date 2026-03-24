---
name: foundry-status
description: Slash command for Azure AI Foundry hosted-agent inventory and health checks through the Pocket Foundry Operator.
user-invocable: true
metadata: {"openclaw":{"homepage":"https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview"}}
---

# Foundry Status

Use this skill for `/foundry_status` requests and any equivalent natural-language request asking for hosted-agent status, inventory, health, or a quick project summary.

## Intent

This is read-only. Do not ask for approval.

## Argument handling

- No argument: return a project-level summary.
- One argument: treat it as an agent name and return targeted status for that agent.
- Extra text: interpret it as optional scope, for example `verbose`, `with telemetry`, or `project`.

## Dispatch contract

Normalize the request into:

```json
{
  "operation": "status",
  "target": {
    "type": "project|agent",
    "name": "<optional agent name>"
  },
  "request": {
    "summary": "Summarize hosted-agent health and recent failures.",
    "args": {
      "verbosity": "standard|verbose",
      "includeTelemetry": false
    },
    "approvalState": "not-required"
  }
}
```

Route the request to `fo-pocket-operator`, which should call `fo-inventory-health`.

Dispatch the normalized JSON to `http://172.18.0.1:8788/dispatch` using the exec tool and `/usr/bin/curl` on host `gateway`.

Use a single `/usr/bin/curl` command with inline `--data` JSON. Do not create temp files. After the curl result returns, do not call any other tool. Summarize the result directly in this chat.

## Output shape

- First line: project or agent health in one sentence.
- Then list:
  - state
  - recent failures if any
  - recommended next action

If the bridge returns an envelope like:

```json
{
  "result": {
    "status": "ok",
    "summary": "...",
    "details": ["..."]
  }
}
```

use `result.summary` and `result.details`.

## If the bridge call fails

Report the bridge failure and print the exact payload that was attempted.

