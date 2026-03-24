param(
    [string]$ResourceGroup = "openclaw-vm-rg",
    [string]$VmName = "openclaw-vm",
    [string]$ProjectEndpoint = "https://hosted-agent.services.ai.azure.com/api/projects/cz-hostedagent-1",
    [string]$OperatorAgentName = "fo-pocket-operator",
    [string]$BridgeDir = "/home/azureuser/foundry-pocket-operator-bridge",
    [int]$Port = 8788
)

$bridgeRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\\openclaw-bridge")).Path
$files = @(
    @{ Local = (Join-Path $bridgeRoot "main.py"); Remote = "$BridgeDir/main.py" }
    @{ Local = (Join-Path $bridgeRoot "requirements.txt"); Remote = "$BridgeDir/requirements.txt" }
)

$lines = @(
    "set -eu",
    "mkdir -p '$BridgeDir'"
)

foreach ($file in $files) {
    $remoteDir = [System.IO.Path]::GetDirectoryName($file.Remote).Replace('\', '/')
    $content = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes((Get-Content -Raw $file.Local)))
    $lines += "mkdir -p '$remoteDir'"
    $lines += "printf '%s' '$content' | base64 -d > '$($file.Remote)'"
}

$envContent = @(
    "AZURE_AI_PROJECT_ENDPOINT=$ProjectEndpoint"
    "FOUNDRY_OPERATOR_AGENT_NAME=$OperatorAgentName"
    "OPENCLAW_BRIDGE_SHARED_SECRET="
    "OPENCLAW_BRIDGE_USE_CONVERSATIONS=false"
) -join "`n"
$envB64 = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($envContent))
$lines += "printf '%s' '$envB64' | base64 -d > '$BridgeDir/.env'"
$lines += "python3 -m pip install --user -r '$BridgeDir/requirements.txt' >/dev/null"
$lines += "pkill -f 'uvicorn main:app .* --port $Port' || true"
$lines += "nohup python3 -m uvicorn main:app --host 0.0.0.0 --port $Port --app-dir '$BridgeDir' > '$BridgeDir/bridge.log' 2>&1 &"
$lines += "sleep 5"
$lines += "curl -fsS http://127.0.0.1:$Port/health"

$scriptPath = Join-Path $env:TEMP "deploy-openclaw-bridge.sh"
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
