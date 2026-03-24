# OpenClaw and Foundry Better Together

Chat-native operations for Microsoft Foundry Hosted Agents, built with OpenClaw, a local bridge layer, and specialist Hosted Agents in Foundry.

This demo shows how to use:

- **OpenClaw** as the user-facing control surface
- **Microsoft Foundry Hosted Agents** as the managed runtime
- **Microsoft Agent Framework patterns** for orchestrated specialist-agent workflows

The result is a "Foundry operator in your pocket": ask for project health, diagnose agent issues, run smoke tests, and plan approved changes from a chat surface such as OpenClaw Control UI, Discord, or Telegram.

## Why This Exists

OpenClaw and Foundry are strong at different layers:

- **OpenClaw** is strong at omnichannel ingress, chat UX, identity, and approvals
- **Foundry Hosted Agents** are strong at managed deployment, identity, versioning, and Microsoft-native runtime
- **Agent Framework-style orchestration** is strong at decomposing one operator request into specialized agent roles

This demo keeps those concerns separate instead of collapsing everything into one monolithic chat agent.

## Architecture

The system has three layers:

1. **OpenClaw**
   - hosts the user-facing skills
   - receives commands such as status, diagnose, smoke, and change
   - runs on the Azure VM

2. **Bridge**
   - runs locally on the OpenClaw VM
   - accepts normalized operator requests from OpenClaw
   - authenticates to Foundry
   - forwards requests to the Foundry entrypoint agent
   - returns normalized JSON back to OpenClaw

3. **Foundry Hosted Agents**
   - `fo-pocket-operator` is the single entrypoint
   - it delegates to specialist agents:
     - `fo-inventory-health`
     - `fo-observability`
     - `fo-smoke`
     - `fo-change-controller`

### Request Flow

```text
+-------------------------------+
| OpenClaw Control UI / Discord |
| / Telegram                    |
+---------------+---------------+
                |
                v
+-------------------------------+
| OpenClaw skill                |
| foundry-status / diagnose /   |
| smoke / change                |
+---------------+---------------+
                |
                v
+-------------------------------+
| Local bridge on OpenClaw VM   |
| normalize + auth + dispatch   |
+---------------+---------------+
                |
                v
+-------------------------------+
| fo-pocket-operator            |
| Foundry entrypoint agent      |
+---------------+---------------+
                |
                v
+-------------------+    +----------------------+
| fo-inventory-     |    | fo-observability     |
| health            |    | telemetry + RCA      |
+-------------------+    +----------------------+

+-------------------+    +----------------------+
| fo-smoke          |    | fo-change-controller |
| invoke + verify   |    | plan + redeploy      |
+-------------------+    +----------------------+

                |
                v
+-------------------------------+
| fo-pocket-operator            |
| aggregate + format result     |
+---------------+---------------+
                |
                v
+-------------------------------+
| Bridge returns JSON           |
| OpenClaw renders reply        |
+-------------------------------+
```

## Components

### OpenClaw-facing layer

- `foundry-status`
  - summarize project or agent health
- `foundry-diagnose`
  - query telemetry-backed diagnosis for one hosted agent
- `foundry-smoke`
  - run a direct smoke test against one hosted agent
- `foundry-change`
  - generate approval-ready change plans and narrow redeploy execution

### Foundry runtime layer

- `fo-pocket-operator`
  - orchestrator and single runtime entrypoint
- `fo-inventory-health`
  - lists agents and summarizes health
- `fo-observability`
  - queries Application Insights telemetry
- `fo-smoke`
  - invokes another hosted agent and verifies behavior
- `fo-change-controller`
  - creates change plans and performs narrow approved redeploys

## Why Use a Bridge

The bridge is deliberate, not accidental.

Without it, OpenClaw skills would need to know:

- Foundry Responses API request shape
- Foundry auth and token acquisition
- API versions
- conversation handling
- response normalization

The bridge keeps the contract simple:

- OpenClaw sends operator intent
- the bridge translates that intent to Foundry calls
- Foundry returns structured results

That gives you:

- cleaner separation of concerns
- one place for auth and protocol handling
- less brittle skill logic
- easier future evolution for retries, logging, and request validation

## Repository Layout

```text
foundry-pocket-operator-demo/
├── README.md
├── .env.example
├── fo-pocket-operator/
├── fo-inventory-health/
├── fo-observability/
├── fo-smoke/
├── fo-change-controller/
├── openclaw-bridge/
└── scripts/
```

## Deployment Boundary

This split is important:

- **OpenClaw VM**
  - resource group: `openclaw-vm-rg`
  - runs OpenClaw and the bridge

- **Foundry Hosted Agents**
  - resource group: `Agents`
  - deployed to the Foundry account / project

The Hosted Agents are intentionally **not** deployed into the VM resource group.

## Models

The current demo uses:

- **OpenClaw chat surface**: `gpt-5.4-mini`
- **Hosted Agents in Foundry**: `gpt-4.1-mini`

All 5 Hosted Agents currently use the same Foundry model deployment, but their behavior differs because their code and role are different.

## Prerequisites

- Azure subscription with:
  - one OpenClaw VM
  - one Microsoft Foundry account + project
  - one Azure Container Registry
  - one Application Insights resource
- `foundry-agent` / `fa` CLI configured for the Foundry project
- OpenClaw already installed and working on the VM
- GitHub CLI if you want to publish the project

## Deploy the Hosted Agents

Prepare local deploy config:

```powershell
pwsh .\scripts\prepare-foundry-deploy.ps1
```

Deploy each Hosted Agent from its folder:

```powershell
cd .\fo-pocket-operator
fa deploy --name fo-pocket-operator

cd ..\fo-inventory-health
fa deploy --name fo-inventory-health

cd ..\fo-observability
fa deploy --name fo-observability

cd ..\fo-smoke
fa deploy --name fo-smoke

cd ..\fo-change-controller
fa deploy --name fo-change-controller
```

## Deploy the Bridge to the OpenClaw VM

```powershell
pwsh .\scripts\deploy-openclaw-bridge-to-vm.ps1
```

The bridge listens on the VM and forwards normalized requests to `fo-pocket-operator`.

## Install the OpenClaw Skills on the VM

```powershell
pwsh .\scripts\push-openclaw-skills-to-vm.ps1
```

These skills are what make the chat-facing operator experience work from OpenClaw.

## Demo Commands

Recommended control-flow demo:

```text
/skill foundry-status
/skill foundry-diagnose wf-orchestrator
/skill foundry-smoke mcp-learn-sample
/skill foundry-change redeploy wf-orchestrator
```

Recommended storytelling order:

1. Show project health
2. Diagnose one target agent
3. Run a smoke test
4. Produce a change plan
5. Approve a safe redeploy
6. Re-run status

## Significance of This Architecture

This demo is interesting because it combines three things that are usually shown separately:

- **OpenClaw** gives you a practical omnichannel operator UX
- **Foundry Hosted Agents** give you a Microsoft-native managed runtime
- **Agent Framework-style specialization** gives you modular workflows instead of one giant prompt-agent

That means the demo is not "just another chatbot."

It shows a real control-plane pattern:

- chat-native ingress
- managed hosted-agent backend
- specialist-agent orchestration
- telemetry-backed diagnosis
- smoke verification
- approval-gated operational changes

## Current Limitations

- The OpenClaw skill execution path is still somewhat model-driven and can be flaky in Control UI
- `redeploy` is the only executed mutation path implemented today
- `restart` and `stop` are not implemented
- Some older Foundry agents can still report `unknown` status because their metadata shape differs from the newer hosted agents
- OpenClaw skill invocation is currently stabilized with practical runtime workarounds on the VM

## Next Improvements

- replace prompt-driven OpenClaw skill execution with direct command-dispatch wiring
- make bridge request/response formatting more deterministic
- add richer verification flows after change execution
- upgrade selected specialist agents to stronger models where needed
- add Teams as another front-end surface

## Files to Start With

If you are reading the code for the first time, start here:

- [openclaw-bridge/main.py](./openclaw-bridge/main.py)
- [fo-pocket-operator/main.py](./fo-pocket-operator/main.py)
- [fo-inventory-health/main.py](./fo-inventory-health/main.py)
- [fo-observability/main.py](./fo-observability/main.py)
- [fo-smoke/main.py](./fo-smoke/main.py)
- [fo-change-controller/main.py](./fo-change-controller/main.py)

## Status

The demo currently supports:

- project status checks
- targeted diagnosis
- direct smoke tests
- approval-gated change planning
- narrow redeploy execution

It is intended as a reference architecture and demo, not yet as a polished production product.
