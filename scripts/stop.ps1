$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
$dataPath = if ($env:NABI_DATA_DIR) { [IO.Path]::GetFullPath($env:NABI_DATA_DIR) } else { Join-Path $env:LOCALAPPDATA 'NabiOffice' }
foreach ($entry in @(@{Name='server'; Script='serve.py'}, @{Name='worker'; Script='manage.py'})) {
    $pidFile = Join-Path $dataPath ($entry.Name + '.pid')
    if (-not (Test-Path -LiteralPath $pidFile)) { continue }
    $taskProcessId = 0
    if (-not [int]::TryParse((Get-Content -LiteralPath $pidFile -Raw).Trim(), [ref]$taskProcessId)) { throw "PID inválido em $pidFile" }
    $taskProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $taskProcessId" -ErrorAction SilentlyContinue
    $scriptPath = Join-Path $projectPath $entry.Script
    if ($taskProcess) {
        if ($taskProcess.ExecutablePath -ine $pythonPath -or $taskProcess.CommandLine -notlike "*$scriptPath*") {
            throw "O PID de $($entry.Name) pertence a outro processo. Nenhum processo foi encerrado por esse registro."
        }
        if ($entry.Name -eq 'worker' -and $taskProcess.CommandLine -notmatch '\brunworker\b') {
            throw 'O processo não é o executor do Nabi Office.'
        }
        Stop-Process -Id $taskProcessId
    }
    Remove-Item -LiteralPath $pidFile -Force
}
Write-Host 'Nabi Office encerrado. Tarefas interrompidas serão verificadas pelo executor na próxima inicialização.'
