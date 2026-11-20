[CmdletBinding()]
param(
 [switch]$EnableRealCalls,
 [ValidateSet('deepseek','qwen','openai_compatible')][string]$Provider='deepseek',
 [string]$BaseUrl='https://api.deepseek.com',[string]$Model='deepseek-flash',[string]$AllowedHost='',
 [Parameter(Mandatory=$true)][double]$InputPricePerMillionCny,
 [Parameter(Mandatory=$true)][double]$OutputPricePerMillionCny,
 [ValidateRange(0.01,1000)][double]$BudgetCny=0.5,
 [ValidateRange(1,4096)][int]$MaxOutputTokens=4096,[ValidateRange(1,300)][int]$TimeoutSeconds=120,
 [int]$BackendPort=19323,[int]$FrontendPort=15323)
$ErrorActionPreference='Stop'
if(-not $EnableRealCalls){throw 'Explicit -EnableRealCalls is required; no key was read.'}
if((Read-Host 'Type ENABLE to authorize enabling the service (startup sends no inference)') -cne 'ENABLE'){throw 'Not authorized.'}
$python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $python)){throw 'Run Install.ps1 first.'}
$secure=$null;$pointer=[IntPtr]::Zero;$plain=$null;$json=$null;$process=$null
try{
 $secure=Read-Host 'Enter your own API Key (hidden)' -AsSecureString
 $pointer=[Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
 $plain=[Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
 $body=@{provider=$Provider;base_url=$BaseUrl;model=$Model;allowed_host=$AllowedHost;input_price=$InputPricePerMillionCny;output_price=$OutputPricePerMillionCny;budget_cny=$BudgetCny;max_output_tokens=$MaxOutputTokens;timeout=$TimeoutSeconds;backend_port=$BackendPort;frontend_port=$FrontendPort;key=$plain}
 $json=ConvertTo-Json -InputObject $body -Compress
 $psi=[Diagnostics.ProcessStartInfo]::new();$psi.FileName=$python;$psi.Arguments='-B "'+(Join-Path $PSScriptRoot 'deployment\real_launch.py')+'"';$psi.WorkingDirectory=$PSScriptRoot
 $psi.UseShellExecute=$false;$psi.CreateNoWindow=$true;$psi.RedirectStandardInput=$true;$psi.RedirectStandardOutput=$true;$psi.RedirectStandardError=$true
 $psi.EnvironmentVariables.Clear()
 foreach($n in @('SystemRoot','WINDIR','COMSPEC','PATH','PATHEXT','TEMP','TMP','APPDATA','LOCALAPPDATA')){$v=[Environment]::GetEnvironmentVariable($n);if($v){$psi.EnvironmentVariables[$n]=$v}}
 $psi.EnvironmentVariables['PYTHONUTF8']='1';$psi.EnvironmentVariables['PYTHONDONTWRITEBYTECODE']='1'
 $process=[Diagnostics.Process]::new();$process.StartInfo=$psi;[void]$process.Start()
 $process.StandardInput.WriteLine($json);$process.StandardInput.Close()
 # Async stream draining avoids a PIPE deadlock. The child has a bounded readiness deadline.
 $outTask=$process.StandardOutput.ReadToEndAsync();$errTask=$process.StandardError.ReadToEndAsync()
 if(-not $process.WaitForExit(45000)){throw 'Startup timed out. Inspect only delivery-owned services before retrying.'}
 Write-Output $outTask.Result
 # Child emits only generic failures; do not print arbitrary exception/input data.
 if($process.ExitCode -ne 0){throw 'Real service was not enabled successfully.'}
}finally{
 if($pointer -ne [IntPtr]::Zero){[Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)}
 if($secure){$secure.Dispose()}
 if($process){$process.Dispose()}
 Remove-Variable plain,json,body,secure,pointer -ErrorAction SilentlyContinue
}
# Immutable string/process memory cannot be guaranteed erased. Nothing writes the key to disk.