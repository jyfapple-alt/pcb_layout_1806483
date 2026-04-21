param(
    [string]$CondaEnv = "auto_routing"
)

$serverPath = Join-Path $PSScriptRoot "pcb_routing_server.py"

if ($env:CONDA_EXE -and (Test-Path $env:CONDA_EXE)) {
    $condaExe = $env:CONDA_EXE
} elseif (Test-Path "$env:USERPROFILE\miniconda3\Scripts\conda.exe") {
    $condaExe = "$env:USERPROFILE\miniconda3\Scripts\conda.exe"
} else {
    $condaExe = "conda"
}

& $condaExe run --no-capture-output -n $CondaEnv python $serverPath
exit $LASTEXITCODE
