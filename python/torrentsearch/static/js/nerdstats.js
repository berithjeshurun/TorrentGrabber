/**
 * YouTube-style "Stats for nerds" overlay for the stream player.
 */

function bufferAhead(video) {
  try {
    const t = video.currentTime;
    for (let i = 0; i < video.buffered.length; i++) {
      const start = video.buffered.start(i);
      const end = video.buffered.end(i);
      if (t >= start && t <= end) return Math.max(0, end - t);
    }
  } catch {
    /* ignore */
  }
  return 0;
}

function fmtKbps(bps) {
  if (!bps || bps <= 0) return "—";
  return `${(bps / 1000).toFixed(0)} kbps`;
}

function fmtBytes(n) {
  if (!n) return "0 B";
  const u = ["B", "KB", "MB", "GB"];
  let i = 0;
  let v = n;
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function videoCodecHint(tracks) {
  const v = tracks?.video?.[0];
  const a = tracks?.audio?.find((t) => t.default) || tracks?.audio?.[0];
  const parts = [];
  if (v) parts.push(`V:${v.codec}${v.width ? ` ${v.width}x${v.height}` : ""}`);
  if (a)
    parts.push(
      `A:${a.codec}${a.channels ? ` ${a.channels}ch` : ""}${
        a.language ? ` ${a.language}` : ""
      }`
    );
  return parts.join(" · ") || "—";
}

/**
 * @param {object} s
 * @param {HTMLVideoElement} s.video
 * @param {any} [s.hls]
 * @param {object|null} [s.download]
 * @param {object|null} [s.tracks]
 * @param {string} [s.mode] hls | progressive
 * @param {string} [s.downloadId]
 */
export function renderNerdStats(s) {
  const { video, hls, download, tracks, mode, downloadId, engineMetrics } = s;
  const q =
    typeof video.getVideoPlaybackQuality === "function"
      ? video.getVideoPlaybackQuality()
      : null;
  const dropped = q?.droppedVideoFrames ?? "—";
  const total = q?.totalVideoFrames ?? "—";
  const buf = bufferAhead(video);
  const res = `${video.videoWidth || "?"}×${video.videoHeight || "?"}`;
  const viewport = `${video.clientWidth}×${video.clientHeight}`;

  let hlsLine = "—";
  if (hls) {
    const level = hls.levels?.[hls.currentLevel];
    const bw = level?.bitrate;
    hlsLine = `L${hls.currentLevel} ${fmtKbps(bw)} · buffered ${
      hls.mainForwardBufferInfo?.len?.toFixed?.(1) ?? "—"
    }s`;
  }

  const peers = download?.num_peers ?? "—";
  const dl = download
    ? `${((download.download_rate || 0) / 1024).toFixed(0)} KB/s`
    : "—";
  const prog = download
    ? `${((download.progress || 0) * 100).toFixed(1)}%`
    : "—";
  const swarm = download
    ? `${fmtBytes(download.downloaded_bytes)} / ${fmtBytes(
        download.total_bytes
      )}`
    : "—";

  const rows = [
    ["Video ID", downloadId || "—"],
    ["Viewport / Optimal Res", `${viewport} / ${res}`],
    ["Current / Optimal Res", `${res} / ${res}`],
    ["Volume / Normalized", `${Math.round((video.volume || 0) * 100)}% / —`],
    ["Codecs", videoCodecHint(tracks)],
    ["Container / Protocol", `${tracks?.format || "—"} / ${mode || "—"}`],
    [
      "Engine metrics",
      engineMetrics?.hls_starts != null
        ? `HLS ${engineMetrics.hls_starts}/${engineMetrics.hls_errors || 0} err · gate ${
            engineMetrics.hls_gate_rejects || 0
          } · wait ${engineMetrics.piece_wait_ms_avg || 0}ms · UR ${
            engineMetrics.underruns || 0
          }`
        : "—",
    ],
    ["Buffer Health", `${buf.toFixed(1)} s`],
    ["Frames dropped", `${dropped} / ${total}`],
    ["HLS", hlsLine],
    ["Torrent peers", String(peers)],
    ["Download speed", dl],
    ["Torrent progress", `${prog} · ${swarm}`],
    ["Engine status", download?.status || "—"],
    [
      "Playhead",
      `${(video.currentTime || 0).toFixed(2)}s / ${(
        video.duration || 0
      ).toFixed(2)}s`,
    ],
  ];

  return rows
    .map(
      ([k, v]) =>
        `<div class="nerd-row"><span class="nerd-k">${escapeHtml(
          k
        )}</span><span class="nerd-v">${escapeHtml(String(v))}</span></div>`
    )
    .join("");
}

function escapeHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
