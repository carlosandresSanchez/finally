# Start FinAlly in Docker (Windows PowerShell). Usage: .\scripts\start_windows.ps1 [-Build] [-NoOpen]
param([switch]$Build, [switch]$NoOpen)
$ErrorActionPreference = "Stop"

$Image = "finally"
$Container = "finally"
$Volume = "finally-data"
$Port = if ($env:PORT) { $env:PORT } else { "8000" }
$Root = Split-Path -Parent $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Write-Error "Docker is required: https://docs.docker.com/get-docker/"
}

docker image inspect $Image *> $null
if ($Build -or $LASTEXITCODE -ne 0) {
    Write-Host "Building $Image image..."
    docker build -t $Image $Root
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

docker rm -f $Container *> $null

$runArgs = @("run", "-d", "--name", $Container, "-p", "${Port}:8000", "-v", "${Volume}:/app/db")
$envFile = Join-Path $Root ".env"
if (Test-Path $envFile) {
    $runArgs += @("--env-file", $envFile)
} else {
    Write-Warning "No .env found; AI chat needs OPENROUTER_API_KEY (see .env.example)."
}
$runArgs += $Image
docker @runArgs | Out-Null
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$Url = "http://localhost:$Port"
Write-Host -NoNewline "Waiting for FinAlly to start"
for ($i = 0; $i -lt 60; $i++) {
    try { Invoke-WebRequest -UseBasicParsing "$Url/api/health" -TimeoutSec 2 | Out-Null; break } catch { Write-Host -NoNewline "."; Start-Sleep 1 }
}
Write-Host ""
Write-Host "FinAlly is running at $Url"
if (-not $NoOpen) { Start-Process $Url }
