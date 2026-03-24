param(
    [string]$ResourceGroup = "openclaw-vm-rg",
    [string]$VmName = "openclaw-vm"
)

$scriptLines = @(
    'set -eu',
    'CONFIG=/home/azureuser/.openclaw/openclaw.json',
    'cp "$CONFIG" "$CONFIG.bak.$(date +%Y%m%d%H%M%S)"',
    "python3 - <<'PY'",
    'import json',
    'from pathlib import Path',
    "path = Path('/home/azureuser/.openclaw/openclaw.json')",
    'cfg = json.loads(path.read_text())',
    "agents = cfg.setdefault('agents', {})",
    "agent_list = agents.setdefault('list', [])",
    'main = None',
    'for item in agent_list:',
    "    if item.get('id') == 'main':",
    '        main = item',
    '        break',
    'if main is None:',
    "    main = {'id': 'main'}",
    '    agent_list.append(main)',
    "tools = main.setdefault('tools', {})",
    "deny = tools.setdefault('deny', [])",
    "if 'message' not in [str(x).lower() for x in deny]:",
    "    deny.append('message')",
    "path.write_text(json.dumps(cfg, indent=2) + '\\n')",
    "print('patched main agent tools.deny:', tools.get('deny'))",
    'PY',
    'cd /home/azureuser/openclaw',
    'docker compose restart openclaw-gateway >/dev/null',
    'sleep 8',
    'docker compose ps openclaw-gateway',
    "sed -n '1,220p' /home/azureuser/.openclaw/openclaw.json"
)

$scriptPath = Join-Path $env:TEMP "patch-openclaw-foundry-skill-agent.sh"
Set-Content -Path $scriptPath -Value ($scriptLines -join "`n") -Encoding utf8

try {
    az vm run-command invoke `
        --resource-group $ResourceGroup `
        --name $VmName `
        --command-id RunShellScript `
        --scripts "@$scriptPath"
}
finally {
    Remove-Item $scriptPath -Force -ErrorAction SilentlyContinue
}
