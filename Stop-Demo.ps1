[CmdletBinding()]
param([ValidateSet('stable','enhanced','replay','real')][string]$Mode='stable')
$ErrorActionPreference='Stop'
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -B (Join-Path $PSScriptRoot 'deployment\demo_processes.py') stop --mode $Mode
exit $LASTEXITCODE