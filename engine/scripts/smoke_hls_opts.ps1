# Smoke: poster + thumbs + HLS with audio bitrate override (needs a media torrent id or local add)
param(
  [string]$EngineUrl = "",
  [string]$Magnet = "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel"
)

$ErrorActionPreference = "Stop"
$exe = Join-Path $PSScriptRoot "..\build\Release\torrent-engine.exe"
if (-not (Test-Path $exe)) { throw "Build Release first" }

$proc = $null
$port = $null
try {
  if (-not $EngineUrl) {
    $ffmpeg = Join-Path $PSScriptRoot "..\..\python\etc"
    $data = Join-Path $PSScriptRoot "..\..\engine-data-smoke-hls"
    New-Item -ItemType Directory -Force -Path $data | Out-Null
    $proc = Start-Process -FilePath $exe -ArgumentList @(
      "--ffmpeg-dir", (Resolve-Path $ffmpeg).Path,
      "--data-dir", (Resolve-Path $data).Path,
      "--bind", "127.0.0.1",
      "--port", "0"
    ) -PassThru -NoNewWindow -RedirectStandardOutput "$env:TEMP\te-hls-out.txt" -RedirectStandardError "$env:TEMP\te-hls-err.txt"
    for ($i = 0; $i -lt 40; $i++) {
      Start-Sleep -Milliseconds 250
      if (Test-Path "$env:TEMP\te-hls-out.txt") {
        $line = Get-Content "$env:TEMP\te-hls-out.txt" -ErrorAction SilentlyContinue | Select-String "ENGINE_READY"
        if ($line) {
          if ($line -match "port=(\d+)") { $port = [int]$Matches[1]; break }
        }
      }
    }
    if (-not $port) { throw "engine did not become ready" }
    $EngineUrl = "http://127.0.0.1:$port"
  }

  Write-Host "Engine: $EngineUrl"
  $add = Invoke-RestMethod -Method Post -Uri "$EngineUrl/v1/torrents" -ContentType "application/json" -Body (@{
    magnet = $Magnet
    mode = "stream"
  } | ConvertTo-Json)
  $id = $add.id
  Write-Host "id=$id"

  $ready = $false
  for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Seconds 2
    try {
      $tr = Invoke-RestMethod -Uri "$EngineUrl/v1/torrents/$id/tracks"
      if (-not $tr.error) { $ready = $true; Write-Host "tracks ok hdr=$($tr.hdr) interlaced=$($tr.interlaced)"; break }
    } catch {}
  }
  if (-not $ready) { throw "tracks not ready" }

  $hls = Invoke-RestMethod -Method Post -Uri "$EngineUrl/v1/torrents/$id/hls?audio_bitrate=256k"
  Write-Host "hls playlist=$($hls.playlist_url)"

  $poster = Invoke-RestMethod -Method Post -Uri "$EngineUrl/v1/torrents/$id/poster"
  Write-Host "poster=$($poster.poster_url)"

  $thumbs = Invoke-RestMethod -Method Post -Uri "$EngineUrl/v1/torrents/$id/thumbs?interval=15"
  Write-Host "thumbs sprite=$($thumbs.sprite_url)"

  $seek = Invoke-RestMethod -Method Post -Uri "$EngineUrl/v1/torrents/$id/hls?start_seconds=30&audio_bitrate=256k"
  Write-Host "seek restart playlist=$($seek.playlist_url) start=$($seek.start_seconds)"

  Write-Host "OK smoke_hls_opts"
}
finally {
  if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue }
}
