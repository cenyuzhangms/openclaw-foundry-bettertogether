---
name: foundry-smoke
description: Slash command for direct smoke tests against Azure AI Foundry hosted agents through the Pocket Foundry Operator.
user-invocable: true
metadata: {"openclaw":{"homepage":"https://learn.microsoft.com/en-us/azure/ai-foundry/agents/overview"}}
---

# Foundry Smoke

Use this skill for `/foundry_smoke` requests and any equivalent natural-language request asking to smoke test, ping, or verify reachability of a hosted agent.

## Intent

This is read-only. Do not ask for approval.

## Argument handling

- If the agent name is missing, ask one concise follow-up question for the target agent.
- When the user supplies an agent name, preserve it exactly. Do not substitute a different agent from prior context or earlier results.
- Default smoke prompt:
  - `Reply with READY, then one sentence describing what you can do.`
- If the user provides a custom prompt, pass it as `smokePrompt`.

## Dispatch contract

Normalize the request into:

```json
{
  "operation": "smoke",
  "target": {
    "type": "agent",
    "name": "<required agent name>"
  },
  "request": {
    "summary": "Run a smoke test against the target hosted agent.",
    "args": {
      "smokePrompt": "Reply with READY, then one sentence describing what you can do."
    },
    "approvalState": "not-required"
  }
}
```

Route the request to `fo-pocket-operator`, which should call `fo-smoke`.

Dispatch the normalized JSON to `http://172.18.0.1:8788/dispatch` using the exec tool and `/usr/bin/curl` on host `gateway`.

Use a single `/usr/bin/curl` command with inline `--data` JSON. Do not create temp files. After the curl result returns, do not call any other tool. Summarize the result directly in this chat.

## Output shape

- First line: pass/fail in one sentence.
- Then list:
  - prompt used
  - response summary
  - recommended next step

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

