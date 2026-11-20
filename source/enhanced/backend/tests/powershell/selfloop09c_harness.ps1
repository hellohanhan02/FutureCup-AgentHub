# Intercepted HTTP commands only. Protocol fixtures never contact a model.
param([string]$Scenario,[string]$ProjectRoot)
$ErrorActionPreference='Stop'
$global:Loop09C=@{posts=0;submissions=0;agents=@();policies=@();flows=@();runs=@()}
$global:Loop09CCatalog=@{real_calls_enabled=$true;
    preparation_safety=@{max_concurrency=1;max_workflow_model_calls=10;max_task_model_calls=3;max_feedback_loops=2;estimated_workflow_cny=0.50};
    providers=@(@{id='deepseek';enabled=$true;credentials_configured=$true;base_url='https://api.deepseek.com';retry=@{max_retries=0};
    models=@(@{id='deepseek-flash';response_formats=@('text');max_output_tokens=1024;max_context_bytes=32768;
        input_price_per_million=1;output_price_per_million=1;currency='CNY'})})}
switch ($Scenario) {
    'missing-price' {$global:Loop09CCatalog.providers[0].models[0].input_price_per_million=$null}
    'over-budget' {$global:Loop09CCatalog.providers[0].models[0].input_price_per_million=1000}
    'missing-budget' {$global:Loop09CCatalog.preparation_safety.estimated_workflow_cny=$null}
    'wrong-currency' {$global:Loop09CCatalog.providers[0].models[0].currency='USD'}
    'provider-retry' {$global:Loop09CCatalog.providers[0].retry.max_retries=1}
    'small-context' {$global:Loop09CCatalog.providers[0].models[0].max_context_bytes=8192}
}
function Get-Content {
    param($LiteralPath,[switch]$Raw)
    $text=Microsoft.PowerShell.Management\Get-Content -LiteralPath $LiteralPath -Raw:$Raw
    if ($Scenario -eq 'template-drift' -and $LiteralPath -eq 'config\deepseek-selfloop09c.json') {
        $value=ConvertFrom-Json -InputObject $text
        $value.workflow.resource_limits.max_calls=3
        return ConvertTo-Json -InputObject $value -Depth 32 -Compress
    }
    return $text
}
function Invoke-RestMethod {
    param([string]$Uri,[string]$Method='Get',[string]$ContentType,[int]$TimeoutSec,$Body)
    if ($Method -eq 'Post') {$global:Loop09C.posts++}
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/health') {return @{status='ok'}}
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/model-providers') {return $global:Loop09CCatalog}
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/verification-capabilities') {
        return @{registered_external_tests=@('order09c-instance-v2','order09c-membership-v2','order09c-dependencies-v2','order09c-priority-v2','order09c-adjacency-v2','order09c-windows-v2','order09c-completion-v2','order09c-gap-v2');self_loop_feedback_modes=@('full','errors_only');challenge09c=@{verifier_version=2;feasible=$true;solution_count=1;spec_sha256=('f'*64)};self_loop_feedback_context_version=$(if ($Scenario -eq 'old-backend') {1} else {2})}
    }
    if ($Method -eq 'Post' -and $Uri -match '/(agents|verification-policies|workflows)$') {
        $v=ConvertFrom-Json -InputObject $Body
        switch ($Matches[1]) {
            'agents' {$global:Loop09C.agents += $v}
            'verification-policies' {$global:Loop09C.policies += $v}
            'workflows' {$global:Loop09C.flows += $v}
        }
        return @{id=$v.id}
    }
    if ($Uri -eq 'http://127.0.0.1:18008/api/v1/workflows/validate') {return @{valid=$true}}
    if ($Method -eq 'Post' -and $Uri -match '/workflows/([^/]+)/executions$') {
        if ($Matches[1] -ne $global:Loop09C.flows[-1].id) {throw 'Stale definition submitted'}
        $global:Loop09C.submissions++;$id=[guid]::NewGuid().ToString('N');$global:Loop09C.runs += $id
        return @{run=@{id=$id}}
    }
    if ($Uri -match '/workflow-runs/([a-f0-9]+)$') {
        if ($Matches[1] -ne $global:Loop09C.runs[-1]) {throw 'Wrong run polled'}
        $success=$Scenario -in @('A','B')
        $calls=if ($Scenario -eq 'E') {0} elseif ($Scenario -in @('A','D')) {1} else {2}
        $loops=if ($Scenario -in @('B','C','repeatability')) {1} else {0}
        # Synthetic service flags test CLI classification, not real model success.
        return @{run=@{state=$(if ($success) {'succeeded'} else {'failed'})};verified=$success;simulated=$false;
            resource_usage=@{model_calls=$calls;feedback_loops=$loops;reported_tokens=0;unknown_usage_calls=$calls};nodes=@(@{id='node'})}
    }
    if ($Uri -match '/self-loops/node/attempts$') {
        if ($Scenario -eq 'E') {return @{ordinal=1;error_code='cost_estimate_unavailable';artifact=@{execution_error='cost_estimate_unavailable'}}}
        if ($Scenario -eq 'D') {return @{ordinal=1;error_code='provider_http_error';artifact=@{execution_error='provider_http_error'}}}
        return @{ordinal=1;artifact=@{execution_error=$null}}
    }
    if ($Uri -match '/self-loops/node/verifications$') {
        return @{status=$(if ($Scenario -in @('A','B')) {'PASSED'} else {'FAILED'});evidence=@()}
    }
    throw 'Unexpected HTTP command blocked'
}
function Join-Path {
    param($Path,$ChildPath)
    if ($ChildPath -eq '.venv\Scripts\python.exe') {return 'Invoke-09CProofFixture'}
    Microsoft.PowerShell.Management\Join-Path -Path $Path -ChildPath $ChildPath
}
function Test-Path {
    param($LiteralPath)
    if ($LiteralPath -eq 'Invoke-09CProofFixture') {return $true}
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
}
function Invoke-09CProofFixture {
    if ($args[0] -ne '-m' -or $args[1] -ne 'futurecup.adapters.order_challenge') {throw 'Unexpected native command blocked'}
    $global:LASTEXITCODE=0
    if ($Scenario -eq 'unsatisfiable') {$global:LASTEXITCODE=1;return '{"feasible":false}'}
    $hash=if ($Scenario -eq 'spec-mismatch') {'e'*64} else {'f'*64}
    @{verifier_version=2;feasible=$true;solution_count=1;spec_sha256=$hash;check_ids=@('order09c-instance-v2','order09c-membership-v2','order09c-dependencies-v2','order09c-priority-v2','order09c-adjacency-v2','order09c-windows-v2','order09c-completion-v2','order09c-gap-v2')} | ConvertTo-Json -Compress
}
Set-Location -LiteralPath $ProjectRoot
$target=Join-Path $ProjectRoot 'scripts\Invoke-DeepSeekSelfLoop09C.ps1'
if ($Scenario -eq 'preflight') {
    & $target
    if ($global:Loop09C.posts -ne 0) {throw 'Default preflight mutated server'}
} elseif ($Scenario -in @('A','B','C','D','E','repeatability')) {
    & $target -AuthorizeRealRequest -ConfirmConfiguredPrices
    if ($Scenario -eq 'repeatability') {& $target -AuthorizeRealRequest -ConfirmConfiguredPrices}
    $expected=if ($Scenario -eq 'repeatability') {2} else {1}
    if ($global:Loop09C.submissions -ne $expected -or @($global:Loop09C.runs | Select-Object -Unique).Count -ne $expected) {throw 'Unexpected submission count'}
    foreach ($flow in $global:Loop09C.flows) {
        $c=$flow.nodes[0].config
        if ($flow.max_concurrency -ne 1 -or $flow.resource_limits.max_calls -ne 2 -or
            $c.limits.max_output_tokens -ne 256 -or $c.limits.max_model_calls -ne 1 -or
            $c.recovery.max_attempts -ne 2 -or $c.recovery.max_feedback_loops -ne 1 -or $c.recovery.retry_execution_errors) {throw 'Definition budget drift'}
        if ($global:Loop09C.agents.id -notcontains $c.agent_id -or $global:Loop09C.policies.id -notcontains $c.verification.id) {throw 'Broken unique references'}
    }
} else {
    $blocked=$false
    try {
        if ($Scenario -eq 'unconfirmed-prices') {& $target -AuthorizeRealRequest}
        else {& $target -AuthorizeRealRequest -ConfirmConfiguredPrices}
    } catch {$blocked=$true}
    if (-not $blocked -or $global:Loop09C.posts -ne 0) {throw 'Unsafe request was not blocked before POST'}
}
'PASS: 09-C protocol fixture only; zero network/model calls.'
