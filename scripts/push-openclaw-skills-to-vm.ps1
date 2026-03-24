param(
    [string]$ResourceGroup = "openclaw-vm-rg",
    [string]$VmName = "openclaw-vm",
    [string]$RemoteWorkspace = "/home/azureuser/.openclaw/workspace",
    [string]$LocalSkillsRoot = (Join-Path $PSScriptRoot "..\\skills")
)

$skillsRoot = (Resolve-Path $LocalSkillsRoot).Path
$skillFiles = Get-ChildItem -Path $skillsRoot -Recurse -Filter SKILL.md

if (-not $skillFiles) {
    throw "No SKILL.md files found under $skillsRoot"
}

$lines = @(
    "set -eu",
    "mkdir -p '$RemoteWorkspace/skills'"
)

foreach ($file in $skillFiles) {
    $relative = $file.FullName.Substring($skillsRoot.Length).TrimStart('\').Replace('\', '/')
    $remotePath = "$RemoteWorkspace/skills/$relative"
    $remoteDir = [System.IO.Path]::GetDirectoryName($remotePath).Replace('\', '/')
    $content = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Content -Raw $file.FullName)))
    $lines += "mkdir -p '$remoteDir'"
    $lines += "printf '%s' '$content' | base64 -d > '$remotePath'"
}

$lines += "cd /home/azureuser/openclaw"
$lines += 'docker compose exec -T openclaw-gateway sh -lc ''set -eu; mkdir -p /app/skills; for name in foundry-status foundry-diagnose foundry-smoke foundry-change foundry-operator-core; do src=/home/node/.openclaw/workspace/skills/$name; dst=/app/skills/$name; rm -rf "$dst"; cp -R "$src" "$dst"; done'''
$lines += 'docker compose exec -T openclaw-gateway sh -lc ''find /app/skills -maxdepth 2 -type f -name SKILL.md | sort | grep foundry-'''

$scriptPath = Join-Path $env:TEMP "push-openclaw-skills.sh"
Set-Content -Path $scriptPath -Value ($lines -join "`n") -Encoding utf8

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
