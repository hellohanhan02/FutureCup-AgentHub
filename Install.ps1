[CmdletBinding()]
param([switch]$CheckOnly,[switch]$IncludeEnhanced,[ValidateRange(30,600)][int]$TimeoutSeconds=300,[string]$PythonExecutable='')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$argsList=@((Join-Path $PSScriptRoot 'deployment\install.py'),'--timeout',"$TimeoutSeconds")
if($IncludeEnhanced){$argsList+='--enhanced'}
if($CheckOnly){$argsList+='--check-only'}
if($PythonExecutable){& $PythonExecutable -B @argsList}else{& py -3.11 @argsList}
exit $LASTEXITCODE
