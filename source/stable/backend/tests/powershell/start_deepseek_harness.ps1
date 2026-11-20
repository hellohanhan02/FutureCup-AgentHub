# Synthetic input only. Native Python/backend launches and port probes are replaced.
param([string]$ProjectRoot,[string]$Expected,[string]$Scenario='credential')
$ErrorActionPreference='Stop'
$global:StarterTest=@{reads=0;launches=0;versionCalls=0;secret=$null}
$global:StarterCandidate=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:FUTURECUP_STARTER_TEST_INPUT))
$global:StarterExpected=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($env:FUTURECUP_STARTER_TEST_EXPECTED))
# Deliberately different from the input. The launcher must not inherit/reuse it.
$env:DEEPSEEK_API_KEY='inherited-synthetic-source-not-validated'
function Join-Path {
    param($Path,$ChildPath)
    if ($ChildPath -eq '.venv\Scripts\python.exe') {return 'Invoke-StarterTestPython'}
    Microsoft.PowerShell.Management\Join-Path -Path $Path -ChildPath $ChildPath
}
function Test-Path {
    param($LiteralPath)
    if ($LiteralPath -eq 'Invoke-StarterTestPython') {return $true}
    Microsoft.PowerShell.Management\Test-Path -LiteralPath $LiteralPath
}
function Get-NetTCPConnection {
    param($LocalPort,$State,$ErrorAction)
    if ($Scenario -eq 'busy-port') {return @{OwningProcess=123}}
}
function Get-Content {
    param($LiteralPath,[switch]$Raw)
    $text=Microsoft.PowerShell.Management\Get-Content -LiteralPath $LiteralPath -Raw:$Raw
    if ($Scenario -eq 'wrong-source' -and $LiteralPath -eq 'config\deepseek-safe.json') {
        $profile=(ConvertFrom-Json -InputObject $text)[0]
        $profile.key_env='TASK_UNAPPROVED_SYNTHETIC_SOURCE'
        return '[' + (ConvertTo-Json -InputObject $profile -Depth 16 -Compress) + ']'
    }
    return $text
}
function Read-Host {
    param($Prompt,[switch]$AsSecureString)
    $global:StarterTest.reads++
    if (-not $AsSecureString) {throw 'Hidden input is required'}
    if ($Scenario -eq 'input-error') {throw 'Synthetic input failure'}
    $s=New-Object Security.SecureString
    foreach ($c in $global:StarterCandidate.ToCharArray()) {$s.AppendChar($c)}
    $global:StarterTest.secret=$s
    return $s
}
function Invoke-StarterTestPython {
    if ($args[0] -eq '-c') {
        $global:StarterTest.versionCalls++
        $global:LASTEXITCODE=0
        if ($Scenario -eq 'wrong-python') {'3.12.0'} else {'3.11.9'}
        return
    }
    if ($args[0] -ne '-m' -or $args[1] -ne 'uvicorn') {throw 'Unexpected native operation blocked'}
    $global:StarterTest.launches++
    if ($Scenario -in @('document-mode','document-budget')) {
        $policy=(ConvertFrom-Json $env:FUTURECUP_LLM_PROVIDERS_JSON)[0]
        $guard=ConvertFrom-Json $env:FUTURECUP_REAL_CALL_SAFETY_JSON
        if ($policy.models[0].max_output_tokens -ne 4096 -or $policy.models[0].max_context_bytes -ne 131072 -or $policy.timeout_seconds -ne 120 -or $policy.retry.max_retries -ne 0) {throw 'Document provider limits invalid'}
        $expectedBudget = if ($Scenario -eq 'document-budget') { 2 } else { .5 }
        if ($guard.profile -ne 'workspace_document' -or $guard.max_workflow_model_calls -ne 6 -or $guard.max_feedback_loops -ne 1 -or $guard.estimated_workflow_cny -ne $expectedBudget -or $guard.max_concurrency -ne 1) {throw 'Document safety invalid'}
        if ($env:FUTURECUP_DATABASE_URL -ne 'sqlite:///.data/user-workspace-11b.sqlite3') {throw 'Document database isolation failed'}
    }
    if ($Expected -ne 'PASS' -and $Scenario -ne 'mock-mode') {throw 'Backend must not launch for invalid input'}
    if ($Scenario -ne 'mock-mode') {
        if ($env:DEEPSEEK_API_KEY -cne $global:StarterExpected -or $env:FUTURECUP_ENABLE_REAL_LLM -ne 'true') {
            throw 'Validated credential handoff failed'
        }
        if ($global:StarterTest.reads -ne 1) {throw 'Credential input was repeated'}
        # Temporaries must be cleared and the SecureString disposed before launch.
        foreach ($name in @('secret','pointer','pythonWhitespace','left','right','count','index','codePoint')) {
            if (Get-Variable -Name $name -Scope 1 -ErrorAction SilentlyContinue) {throw 'Sensitive temporary survived validation'}
        }
        $disposed=$false
        try {$null=$global:StarterTest.secret.Copy()} catch [ObjectDisposedException] {$disposed=$true}
        if (-not $disposed) {throw 'SecureString was not disposed before launch'}
    } elseif ($env:FUTURECUP_ENABLE_REAL_LLM -ne 'false') {throw 'Mock mode enabled real calls'}
    $global:LASTEXITCODE=0
}
Set-Location -LiteralPath $ProjectRoot
$target=Microsoft.PowerShell.Management\Join-Path -Path $ProjectRoot -ChildPath 'scripts\Start-DeepSeekSafe.ps1'
$caught=$false
try {
    if ($Scenario -eq 'mock-mode') { & $target }
    elseif ($Scenario -eq 'missing-prices') { & $target -EnableRealCalls }
    elseif ($Scenario -eq 'bad-price') { & $target -EnableRealCalls -InputPricePerMillionCny 0 -OutputPricePerMillionCny 1 }
    elseif ($Scenario -eq 'document-mode') { & $target -WorkspaceDocument -Port 18013 -EnableRealCalls -InputPricePerMillionCny 1 -OutputPricePerMillionCny 1 }
    elseif ($Scenario -eq 'document-budget') { & $target -WorkspaceDocument -Port 18013 -BudgetCny 2 -EnableRealCalls -InputPricePerMillionCny 1 -OutputPricePerMillionCny 1 }
    else { & $target -EnableRealCalls -InputPricePerMillionCny 1 -OutputPricePerMillionCny 1 }
} catch {$caught=$true}
if ($Scenario -in @('busy-port','wrong-python','missing-prices','bad-price','wrong-source')) {
    if (-not $caught -or $global:StarterTest.reads -ne 0 -or $global:StarterTest.launches -ne 0) {throw 'Startup safety regression'}
} else {
    if ($caught) {throw 'Unexpected launcher failure'}
    if ($Expected -eq 'FAIL' -and $LASTEXITCODE -ne 1) {throw 'Invalid input did not report a failing exit status'}
    if ($Expected -eq 'PASS' -or $Scenario -eq 'mock-mode') {
        if ($global:StarterTest.launches -ne 1) {throw 'Expected exactly one backend launch'}
    } elseif ($global:StarterTest.launches -ne 0) {throw 'Invalid input launched backend'}
    if ($Scenario -eq 'mock-mode' -and $global:StarterTest.reads -ne 0) {throw 'Mock mode prompted for credentials'}
    if ($Scenario -ne 'mock-mode' -and $global:StarterTest.reads -ne 1) {throw 'Unexpected prompt count'}
    if ($env:DEEPSEEK_API_KEY -or $env:FUTURECUP_ENABLE_REAL_LLM -ne 'false') {throw 'Credential environment not cleaned'}
}
# No output other than the launcher's PASS/FAIL. Assertions never contain input.
