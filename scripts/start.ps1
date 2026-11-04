$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$markerPath = Join-Path $projectPath '.venv\nabi-installed.txt'
$dependencyHash = (Get-FileHash -LiteralPath (Join-Path $projectPath 'requirements.txt') -Algorithm SHA256).Hash
$installedHash = if (Test-Path -LiteralPath $markerPath) { (Get-Content -LiteralPath $markerPath -Raw).Trim() } else { '' }
$dataPath = if ($env:NABI_DATA_DIR) { [IO.Path]::GetFullPath($env:NABI_DATA_DIR) } else { Join-Path $env:LOCALAPPDATA 'NabiOffice' }
if (-not (Test-Path -LiteralPath $pythonPath) -or $dependencyHash -ne $installedHash -or -not (Test-Path -LiteralPath (Join-Path $dataPath 'office.sqlite3'))) { & (Join-Path $PSScriptRoot 'install.ps1') }
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Instalação incompleta.' }
New-Item -ItemType Directory -Force -Path $dataPath | Out-Null
$logPath = Join-Path $dataPath 'logs'
New-Item -ItemType Directory -Force -Path $logPath | Out-Null

function Test-OwnedProcess([string]$pidFile, [string]$scriptPath) {
    if (-not (Test-Path -LiteralPath $pidFile)) { return $false }
    $taskProcessId = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$taskProcessId)) { return $false }
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $taskProcessId" -ErrorAction SilentlyContinue
    return $taskProcess -and $taskProcess.ExecutablePath -ieq $pythonPath -and $taskProcess.CommandLine -like "*$scriptPath*" -and ($scriptPath -notlike '*manage.py' -or $taskProcess.CommandLine -match '\brunworker\b')
}

$serverScript = Join-Path $projectPath 'serve.py'
$workerScript = Join-Path $projectPath 'manage.py'
$serverPid = Join-Path $dataPath 'server.pid'
$workerPid = Join-Path $dataPath 'worker.pid'
if (-not (Test-OwnedProcess $serverPid $serverScript)) {
    $serverProcess = Start-Process -FilePath $pythonPath -ArgumentList @(('"' + $serverScript + '"')) -WorkingDirectory $projectPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'server.log') -RedirectStandardError (Join-Path $logPath 'server-error.log')
    Set-Content -LiteralPath $serverPid -Value $serverProcess.Id -Encoding ascii
}
if (-not (Test-OwnedProcess $workerPid $workerScript)) {
    $workerProcess = Start-Process -FilePath $pythonPath -ArgumentList @(('"' + $workerScript + '"'), 'runworker') -WorkingDirectory $projectPath -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logPath 'worker.log') -RedirectStandardError (Join-Path $logPath 'worker-error.log')
    Set-Content -LiteralPath $workerPid -Value $workerProcess.Id -Encoding ascii
}
$ready = $false
for ($attempt = 0; $attempt -lt 25; $attempt++) {
    try {
        $response = Invoke-RestMethod -Uri 'http://127.0.0.1:8787/health' -TimeoutSec 1
        if ($response.status -eq 'ok' -and $response.product -eq 'Nabi Office' -and (Test-OwnedProcess $serverPid $serverScript)) { $ready = $true; break }
    } catch { Start-Sleep -Milliseconds 400 }
}
if (-not $ready) { throw "O servidor não respondeu. Consulte os logs em $logPath." }
Start-Process 'http://127.0.0.1:8787'
Write-Host 'Nabi Office aberto em http://127.0.0.1:8787. Para encerrar: scripts\stop.ps1.'
