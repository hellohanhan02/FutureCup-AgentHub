# SIMULATED HTTP COMMANDS ONLY. No sockets, model calls, or credential reads.
param([string]$Scenario, [string]$ProjectRoot)
$ErrorActionPreference = 'Stop'
$global:Serial09ATest = @{submissions=0;posts=0;agents=@();policies=@();flows=@();runs=@()}
$global:Serial09ACatalog = @{
    real_calls_enabled=$true;preparation_safety=@{max_concurrency=1;estimated_workflow_cny=0.50;max_workflow_model_calls=10};
    providers=@(@{id='deepseek';enabled=$true;credentials_configured=$true;base_url='https://api.deepseek.com';
        retry=@{max_retries=0};models=@(@{id='deepseek-flash';response_formats=@('json_object');max_output_tokens=1024;
        max_context_bytes=32768;currency='CNY';input_price_per_million=1;output_price_per_million=1})})
}
switch ($Scenario) {
    'missing-price' {$global:Serial09ACatalog.providers[0].models[0].input_price_per_million=$null}
    'wrong-currency' {$global:Serial09ACatalog.providers[0].models[0].currency='USD'}
    'over-budget' {$global:Serial09ACatalog.providers[0].models[0].input_price_per_million=1000}
    'missing-budget' {$global:Serial09ACatalog.preparation_safety.estimated_workflow_cny=$null}
    'transport-retry' {$global:Serial09ACatalog.providers[0].retry.max_retries=1}
    'mock-only' {$global:Serial09ACatalog.real_calls_enabled=$false}
}
function Invoke-RestMethod {
    param([string]$Uri,[string]$Method='Get',[string]$ContentType,[int]$TimeoutSec,$Body)
    if ($Method -eq 'Post') {$global:Serial09ATest.posts++}
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/health') {return @{status='ok'}}
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/model-providers') {return $global:Serial09ACatalog}
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/verification-capabilities') {
        if ($Scenario -eq 'missing-verifier') {return @{registered_external_tests=@()}}
        return @{registered_external_tests=@('serial09a-analysis-v1','serial09a-design-v1','serial09a-review-v1')}
    }
    if ($Method -eq 'Post' -and $Uri -match '/(agents|verification-policies|workflows)$') {
        $value=ConvertFrom-Json -InputObject $Body
        switch ($Matches[1]) {
            'agents' {$global:Serial09ATest.agents += $value}
            'verification-policies' {$global:Serial09ATest.policies += $value}
            'workflows' {$global:Serial09ATest.flows += $value}
        }
        return @{id=$value.id}
    }
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/workflows/validate') {return @{valid=$true}}
    if ($Method -eq 'Post' -and $Uri -match '/workflows/([^/]+)/executions$') {
        if ($Matches[1] -ne $global:Serial09ATest.flows[-1].id) {throw 'Stale workflow submitted'}
        $id=[guid]::NewGuid().ToString('N');$global:Serial09ATest.runs += $id;$global:Serial09ATest.submissions++
        return @{run=@{id=$id}}
    }
    if ($Uri -match '/workflow-runs/([a-f0-9]+)$') {
        if ($Matches[1] -ne $global:Serial09ATest.runs[-1]) {throw 'Stale run polled'}
        # Fixture failure only, no claim of a real model result.
        return @{run=@{state='failed'};verified=$false;simulated=$true;error_code='SIMULATED_TEST_ONLY';
            resource_usage=@{model_calls=1;reported_tokens=0;unknown_usage_calls=1};nodes=@()}
    }
    throw 'Unexpected HTTP operation blocked by test double'
}
$target=Join-Path $ProjectRoot 'scripts\Invoke-DeepSeekSerial09A.ps1'
Set-Location -LiteralPath $ProjectRoot
if ($Scenario -eq 'preflight') {
    & $target
    if ($global:Serial09ATest.posts -ne 0) {throw 'Default mode mutated state'}
} elseif ($Scenario -eq 'repeatability') {
    & $target -AuthorizeRealRequest -ConfirmConfiguredPrices
    & $target -AuthorizeRealRequest -ConfirmConfiguredPrices
    if ($global:Serial09ATest.submissions -ne 2 -or ($global:Serial09ATest.runs | Select-Object -Unique).Count -ne 2) {throw 'Submission reuse'}
    if (($global:Serial09ATest.agents.id | Select-Object -Unique).Count -ne 6) {throw 'Agent ID reuse'}
    if (($global:Serial09ATest.policies.id | Select-Object -Unique).Count -ne 6) {throw 'Policy ID reuse'}
    if (($global:Serial09ATest.flows.id | Select-Object -Unique).Count -ne 2) {throw 'Workflow ID reuse'}
    foreach ($flow in $global:Serial09ATest.flows) {
        if ($flow.max_concurrency -ne 1 -or $flow.resource_limits.max_calls -ne 3) {throw 'Workflow budget drift'}
        foreach ($n in $flow.nodes) {
            if ($n.config.limits.max_model_calls -ne 1 -or $n.config.limits.max_output_tokens -ne 256 -or
                $n.config.recovery.max_attempts -ne 1 -or $n.config.recovery.max_feedback_loops -ne 0) {throw 'Node budget drift'}
            if ($global:Serial09ATest.agents.id -notcontains $n.config.agent_id -or
                $global:Serial09ATest.policies.id -notcontains $n.config.verification.id) {throw 'Broken ID mapping'}
        }
    }
} else {
    $blocked=$false
    try {
        if ($Scenario -eq 'unconfirmed-prices') { & $target -AuthorizeRealRequest }
        else { & $target -AuthorizeRealRequest -ConfirmConfiguredPrices }
    } catch { $blocked=$true }
    if (-not $blocked -or $global:Serial09ATest.posts -ne 0) {throw 'Unsafe preparation was not blocked before POST'}
}
Write-Output "PASS: SIMULATED command harness $Scenario; zero network/model calls."
