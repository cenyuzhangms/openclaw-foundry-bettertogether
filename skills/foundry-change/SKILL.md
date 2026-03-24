---
name: foundry-change
description: Slash command for approval-gated change planning and controlled mutations against Azure AI Foundry hosted agents through the Pocket Foundry Operator.
user-invocable: true
metadata: {"openclaw":{"homepage":"https://learn.microsoft.com/en-us/agent-framework/user-guide/workflows/orchestrations/human-in-the-loop"}}
---

# Foundry Change

Use this skill for `/foundry_change` requests and any equivalent natural-language request to restart, stop, redeploy, scale, or otherwise mutate a hosted agent.

## Safety

Never execute a change on the first pass.

First produce a change plan with:

- target
- requested action
- expected impact
- rollback
- verification step

Only dispatch the mutation after the user gives explicit approval.

## Argument handling

- If target agent or action is missing, ask one short clarification question.
- If the user includes approval language such as `approved`, `proceed`, or `yes, do it`, treat the request as approval only if a concrete plan was already established in the conversation.

## Planning contract

For the initial planning pass, normalize into:

```json
{
  "operation": "change",
  "target": {
    "type": "agent",
    "name": "<required agent name>"
  },
  "request": {
    "summary": "<requested mutation>",
    "args": {
      "action": "restart|stop|redeploy|scale|enable|disable",
      "mode": "plan"
    },
    "approvalState": "plan-only"
  }
}
```

Route the request to `fo-pocket-operator`, which should call `fo-change-controller` only for planning at this stage.

Dispatch the normalized JSON to `http://172.18.0.1:8788/dispatch` using the exec tool and `/usr/bin/curl` on host `gateway`.

Use a single `/usr/bin/curl` command with inline `--data` JSON. Do not create temp files. After the curl result returns, do not call any other tool. Summarize the result directly in this chat.

## Approved execution contract

After explicit approval, normalize into:

```json
{
  "operation": "change",
  "target": {
    "type": "agent",
    "name": "<required agent name>"
  },
  "request": {
    "summary": "<approved mutation>",
    "args": {
      "action": "restart|stop|redeploy|scale|enable|disable",
      "mode": "execute"
    },
    "approvalState": "approved"
  }
}
```

After execution, ask for verification through `fo-smoke` when appropriate.

## Output shape

- Planning reply: concise plan plus an explicit approval prompt.
- Execution reply: action result plus verification result or next verification step.

## If the bridge call fails

Report the bridge failure and print the exact payload that was attempted.

