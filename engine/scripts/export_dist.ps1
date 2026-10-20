# Export Release build + OpenAPI as the SDK surface into engine/dist/
param(
  [string]$Configuration = "Release",
  [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"
$EngineRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutDir) { $OutDir = Join-Path $EngineRoot "dist" }

$BuildRelease = Join-Path $EngineRoot "build\$Configuration"
$Exe = Join-Path $BuildRelease "torrent-engine.exe"
if (-not (Test-Path $Exe)) {
  throw "Missing $Exe - build Release first."
}

$Dest = Join-Path $OutDir "torrent-engine-0.1.0"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null

Copy-Item $Exe $Dest -Force
Get-ChildItem $BuildRelease -Filter "*.dll" | ForEach-Object {
  Copy-Item $_.FullName $Dest -Force
}

Copy-Item (Join-Path $EngineRoot "config.yaml") $Dest -Force
Copy-Item (Join-Path $EngineRoot "openapi.yaml") $Dest -Force
Copy-Item (Join-Path $EngineRoot "README.md") $Dest -Force

$TestPlayer = Join-Path $EngineRoot "test-player"
if (Test-Path (Join-Path $TestPlayer "index.html")) {
  $TpDest = Join-Path $Dest "test-player"
  New-Item -ItemType Directory -Force -Path $TpDest | Out-Null
  Copy-Item (Join-Path $TestPlayer "index.html") $TpDest -Force
}

$Luts = Join-Path $EngineRoot "luts"
if (Test-Path $Luts) {
  Copy-Item $Luts (Join-Path $Dest "luts") -Recurse -Force
}

$FfmpegSrc = Join-Path (Split-Path -Parent $EngineRoot) "python\etc"
if (Test-Path (Join-Path $FfmpegSrc "ffmpeg.exe")) {
  $FfDest = Join-Path $Dest "ffmpeg"
  New-Item -ItemType Directory -Force -Path $FfDest | Out-Null
  Copy-Item (Join-Path $FfmpegSrc "ffmpeg.exe") $FfDest -Force
  $Probe = Join-Path $FfmpegSrc "ffprobe.exe"
  if (Test-Path $Probe) { Copy-Item $Probe $FfDest -Force }
}

$Latest = Join-Path $OutDir "latest"
if (Test-Path $Latest) { Remove-Item $Latest -Recurse -Force }
Copy-Item $Dest $Latest -Recurse -Force

Write-Host "Exported SDK folder:"
Write-Host "  $Dest"
Write-Host "  $Latest"
