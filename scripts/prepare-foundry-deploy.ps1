param(
    [string]$ProjectEndpoint = "https://hosted-agent.services.ai.azure.com/api/projects/cz-hostedagent-1",
    [string]$ModelDeploymentName = "gpt-4.1-mini",
    [string]$AcrName = "czagentacr",
    [string]$SubscriptionId = "174f53f1-d5f0-421e-9358-e6f8020453a8",
    [string]$AppInsightsAppId = "f1eabc22-446b-40ce-9471-63fd3b6ca962"
)

$root = Split-Path -Parent $PSScriptRoot
$agentDirs = @(
    "fo-pocket-operator",
    "fo-inventory-health",
    "fo-observability",
    "fo-smoke",
    "fo-change-controller"
)

foreach ($dir in $agentDirs) {
    $agentPath = Join-Path $root $dir
    $envPath = Join-Path $agentPath ".env"
    $configPath = Join-Path $agentPath ".foundry-agent.json"

    @(
        "AZURE_AI_PROJECT_ENDPOINT=$ProjectEndpoint"
        "MODEL_DEPLOYMENT_NAME=$ModelDeploymentName"
        "ENABLE_APPLICATION_INSIGHTS_LOGGER=false"
        "APP_INSIGHTS_APP_ID=$AppInsightsAppId"
    ) | Set-Content -Path $envPath -Encoding utf8

    @{
        acr = $AcrName
        subscription_id = $SubscriptionId
    } | ConvertTo-Json | Set-Content -Path $configPath -Encoding utf8
}
