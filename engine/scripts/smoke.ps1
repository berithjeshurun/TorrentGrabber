# Smoke-test torrent-engine (Sintel stream + Range media)
param(
  [string]$Engine = "",
  [string]$FfmpegDir = ""
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
if (-not $Engine) {
  $cand = @(
    (Join-Path $root "engine\build\Release\torrent-engine.exe"),
    (Join-Path $root "engine\build\Debug\torrent-engine.exe")
  )
  $Engine = $cand | Where-Object { Test-Path $_ } | Select-Object -First 1
}
if (-not $Engine) { throw "torrent-engine.exe not found - build Release first" }
if (-not $FfmpegDir) { $FfmpegDir = Join-Path $root "python\etc" }

$data = Join-Path $root "engine-data-smoke"
New-Item -ItemType Directory -Force -Path $data | Out-Null

Write-Host "Starting $Engine"
$proc = Start-Process -FilePath $Engine -ArgumentList @(
  "--port","0",
  "--data-dir", $data,
  "--ffmpeg-dir", $FfmpegDir
) -RedirectStandardOutput (Join-Path $data "stdout.txt") -RedirectStandardError (Join-Path $data "stderr.txt") -PassThru -NoNewWindow

$port = $null
for ($i = 0; $i -lt 60; $i++) {
  Start-Sleep -Milliseconds 500
  if (Test-Path (Join-Path $data "stdout.txt")) {
    $line = Get-Content (Join-Path $data "stdout.txt") -ErrorAction SilentlyContinue | Where-Object { $_ -match "ENGINE_READY port=" } | Select-Object -Last 1
    if ($line -match "port=(\d+)") { $port = [int]$Matches[1]; break }
  }
  if ($proc.HasExited) { throw "engine exited early - see $data\stderr.txt" }
}
if (-not $port) { throw "ENGINE_READY not seen" }
Write-Host "Engine on port $port"
$base = "http://127.0.0.1:$port"

try {
  $health = Invoke-RestMethod "$base/v1/health"
  Write-Host "health ok=$($health.ok) version=$($health.version)"

  $magnet = "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337&ws=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2F&xs=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2Fsintel.torrent"
  $add = Invoke-RestMethod -Method Post -Uri "$base/v1/torrents" -ContentType "application/json" -Body (@{ magnet = $magnet; mode = "stream" } | ConvertTo-Json)
  $id = $add.id
  Write-Host "added id=$id status=$($add.status)"

  $ready = $false
  for ($i = 0; $i -lt 90; $i++) {
    Start-Sleep -Seconds 2
    $st = Invoke-RestMethod "$base/v1/torrents/$id"
    Write-Host ("  peers={0} progress={1:p1} status={2} name={3}" -f $st.num_peers, $st.progress, $st.status, $st.name)
    if ($st.num_peers -gt 0 -or $st.progress -gt 0 -or $st.status -eq "streaming" -or $st.status -eq "metadata" -or $st.name) {
      try {
        $req = [System.Net.HttpWebRequest]::Create("$base/v1/torrents/$id/media")
        $req.AddRange(0, 1)
        $req.Timeout = 30000
        $resp = $req.GetResponse()
        $code = [int]$resp.StatusCode
        $ctype = $resp.ContentType
        $resp.Close()
        Write-Host "media status=$code type=$ctype"
        if ($code -eq 206 -or $code -eq 200) { $ready = $true; break }
      } catch {
        Write-Host "  media not ready: $($_.Exception.Message)"
      }
    }
  }
  if (-not $ready) { throw "media Range smoke failed" }
  Write-Host "SMOKE OK"
} finally {
  if ($proc -and -not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
}
