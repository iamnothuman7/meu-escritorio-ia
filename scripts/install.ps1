$ErrorActionPreference = 'Stop'
$projectPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$pythonPath = Join-Path $projectPath '.venv\Scripts\python.exe'
Push-Location -LiteralPath $projectPath
try {
    if (-not (Test-Path -LiteralPath $pythonPath)) {
        $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
        if (-not $pythonCommand) { throw 'Instale Python 3.11 ou 3.12 e execute novamente.' }
        & $pythonCommand.Source -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Não foi possível criar o ambiente Python.' }
    }
    & $pythonPath -m pip install --disable-pip-version-check -r (Join-Path $projectPath 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao instalar dependências.' }
    & $pythonPath (Join-Path $projectPath 'manage.py') migrate --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao aplicar migrações.' }
    & $pythonPath (Join-Path $projectPath 'manage.py') collectstatic --noinput
    if ($LASTEXITCODE -ne 0) { throw 'Falha ao preparar arquivos da interface.' }
    $dependencyHash = (Get-FileHash -LiteralPath (Join-Path $projectPath 'requirements.txt') -Algorithm SHA256).Hash
    Set-Content -LiteralPath (Join-Path $projectPath '.venv\nabi-installed.txt') -Value $dependencyHash -Encoding ascii
    Write-Host 'Nabio Office instalado. Execute scripts\start.ps1 para abrir.'
} finally {
    Pop-Location
}
