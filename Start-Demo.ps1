[CmdletBinding()]
param([ValidateSet('stable','enhanced','replay')][string]$Mode='stable',[int]$BackendPort=0,[int]$FrontendPort=0)
$ErrorActionPreference='Stop'
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $python)){throw 'Run Install.ps1 first.'}
& $python -B (Join-Path $PSScriptRoot 'deployment\demo_processes.py') start --mode $Mode --backend-port $BackendPort --frontend-port $FrontendPort
exit $LASTEXITCODE