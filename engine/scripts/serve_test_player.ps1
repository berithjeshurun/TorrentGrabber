# Serve engine/test-player and open it in the browser.
# The player talks to torrent-engine on 127.0.0.1 (CORS already enabled).
param(
  [int]$Port = 8765,
  [string]$EngineUrl = ""
)

$ErrorActionPreference = "Stop"
$Root = Join-Path (Split-Path -Parent $PSScriptRoot) "test-player"
if (-not (Test-Path (Join-Path $Root "index.html"))) {
  throw "Missing $Root\index.html"
}

$url = "http://127.0.0.1:$Port/"
if ($EngineUrl) {
  $enc = [uri]::EscapeDataString($EngineUrl)
  $url = "http://127.0.0.1:$Port/?engine=$enc"
}

Write-Host "Serving test player from $Root"
Write-Host "Open: $url"
Write-Host "Point the player at ENGINE_READY port (e.g. http://127.0.0.1:17890)"
Write-Host "Ctrl+C to stop."

Start-Process $url

Push-Location $Root
try {
  python -m http.server $Port --bind 127.0.0.1
} finally {
  Pop-Location
}
