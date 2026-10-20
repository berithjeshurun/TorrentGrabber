# Smoke: C++ engine tracks + HLS (Sintel) via Python CppEngineClient
$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
Write-Host "Running tracks/HLS smoke..."
python (Join-Path $PSScriptRoot "smoke_tracks.py")
