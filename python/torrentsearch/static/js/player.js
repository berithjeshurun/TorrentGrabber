/**
 * Server-side stream player + Cinema chrome + Stats for nerds.
 * - MP4/WebM → direct /media
 * - MKV / multi-track → ffmpeg HLS
 */

import { CinemaPlayer } from "./cinema.js";
import { renderNerdStats } from "./nerdstats.js";
import { connectStreamWs } from "./stream_ws.js";

export const DEMO_MAGNET =
  "magnet:?xt=urn:btih:08ada5a7a6183aae1e09d831df6748d566095a10&dn=Sintel&tr=udp%3A%2F%2Ftracker.opentrackr.org%3A1337&tr=udp%3A%2F%2Fopen.stealth.si%3A80%2Fannounce&ws=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2F&xs=https%3A%2F%2Fwebtorrent.io%2Ftorrents%2Fsintel.torrent";

let statsTimer = null;
let nerdTimer = null;
let activeDownloadId = null;
let hlsInstance = null;
let cinema = null;
let lastTracks = null;
let playMode = "progressive";
let nerdOpen = false;
let lastDownloadSnap = null;
let streamWs = null;
let lastEngineMetrics = null;

let trackState = {
  audio: null,
  subtitle: null,
  needsHls: false,
  burnSubtitle: null,
};

let hlsOriginSec = 0;
let rawVttText = null;
let subBlobUrl = null;

let hlsSeekHandler = null;
let hlsSeekVideo = null;

function clearTimers() {
  if (statsTimer) {
    clearInterval(statsTimer);
    statsTimer = null;
  }
  if (nerdTimer) {
    clearInterval(nerdTimer);
    nerdTimer = null;
  }
}

function destroyHls() {
  if (hlsSeekVideo && hlsSeekHandler) {
    hlsSeekVideo.removeEventListener("seeking", hlsSeekHandler);
    hlsSeekVideo = null;
    hlsSeekHandler = null;
  }
  if (hlsInstance) {
    try {
      hlsInstance.destroy();
    } catch {
      /* ignore */
    }
    hlsInstance = null;
  }
}

function parseVttTime(s) {
  const p = String(s).trim().split(":");
  if (p.length === 3) return (+p[0]) * 3600 + (+p[1]) * 60 + parseFloat(p[2]);
  if (p.length === 2) return (+p[0]) * 60 + parseFloat(p[1]);
  return parseFloat(s) || 0;
}

function formatVttTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) sec = 0;
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = sec - h * 3600 - m * 60;
  const whole = Math.floor(s);
  const ms = Math.round((s - whole) * 1000);
  const frac = String(ms).padStart(3, "0");
  if (h > 0) {
    return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${frac}`;
  }
  return `${String(m).padStart(2, "0")}:${String(whole).padStart(2, "0")}.${frac}`;
}

function shiftVtt(text, offsetSec) {
  if (!offsetSec || Math.abs(offsetSec) < 0.05) return text;
  const re =
    /^(\d{1,2}:\d{2}(?::\d{2})?\.\d{1,3})\s*-->\s*(\d{1,2}:\d{2}(?::\d{2})?\.\d{1,3})(.*)$/;
  const out = [];
  for (const line of text.split(/\r?\n/)) {
    const m = line.match(re);
    if (!m) {
      out.push(line);
      continue;
    }
    let a = parseVttTime(m[1]) - offsetSec;
    let b = parseVttTime(m[2]) - offsetSec;
    if (b <= 0) continue;
    if (a < 0) a = 0;
    out.push(`${formatVttTime(a)} --> ${formatVttTime(b)}${m[3] || ""}`);
  }
  return out.join("\n");
}

function ensureCinema(video) {
  const root = document.getElementById("cinema");
  if (!root || !video) return null;
  if (cinema) return cinema;

  cinema = new CinemaPlayer({
    root,
    video,
    onToggleStats: () => setNerdOpen(!nerdOpen),
    onToggleTracks: () => {
      const wrap = document.getElementById("player-tracks");
      if (!wrap) return;
      wrap.classList.toggle("hidden");
      if (!wrap.classList.contains("hidden")) {
        wrap.scrollIntoView({ behavior: "smooth", block: "nearest" });
      }
    },
    onBoost: async () => {
      if (!activeDownloadId) return;
      try {
        await fetch(`/api/v1/downloads/${activeDownloadId}/boost`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ duration: 45, multiplier: 3 }),
        });
        cinema?.setLiveHint(true, "BOOST AHEAD");
      } catch {
        /* ignore */
      }
    },
    onCast: async () => {
      if (!activeDownloadId) return;
      const statusEl = document.getElementById("player-status");
      const status = (m) => {
        if (statusEl) statusEl.textContent = m;
      };
      try {
        status("Preparing cast session…");
        const target =
          window.prompt(
            "Cast target: chromecast | airplay | dlna",
            "chromecast"
          ) || "chromecast";
        // Prefer webtorrent-cli sidecar when configured; else cast session URL.
        const side = await fetch(
          `/api/v1/downloads/${activeDownloadId}/cast/sidecar?target=${encodeURIComponent(
            target
          )}`,
          { method: "POST" }
        );
        if (side.ok) {
          const data = await side.json();
          cinema?.setLiveHint(true, `CAST · ${target.toUpperCase()}`);
          status(data.message || `Casting via webtorrent-cli (${target})`);
          return;
        }
        const res = await fetch(
          `/api/v1/downloads/${activeDownloadId}/cast/session?profile=${encodeURIComponent(
            target === "airplay" ? "airplay" : "chromecast"
          )}`,
          { method: "POST" }
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(
            data.detail || data.error || `Cast failed (${res.status})`
          );
        }
        const url = data.cast_url || data.url || "";
        if (url) {
          await navigator.clipboard?.writeText?.(url);
          window.open(url, "_blank", "noopener");
          window.prompt("Cast URL (copied — open on TV / cast app):", url);
        }
        cinema?.setLiveHint(true, "CAST READY");
        status("Cast session ready");
      } catch (err) {
        status(err.message || String(err));
        window.alert(err.message || String(err));
      }
    },
    onInvite: async () => {
      if (!activeDownloadId) return;
      try {
        const res = await fetch(`/api/v1/downloads/${activeDownloadId}/invite`);
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || data.error || "invite failed");
        const text = data.qr_text || data.deep_link || data.magnet || "";
        await navigator.clipboard?.writeText?.(text);
        window.prompt("Friend invite (copied). Encode qr_text as QR in any app:", text);
      } catch (err) {
        window.alert(err.message || String(err));
      }
    },
    onAbr: async (height) => {
      if (!activeDownloadId) return;
      try {
        await fetch(`/api/v1/downloads/${activeDownloadId}/abr`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ height }),
        });
        // Prefer matching hls.js level by height when available
        if (hlsInstance?.levels?.length) {
          if (!height) {
            hlsInstance.currentLevel = -1;
          } else {
            const idx = hlsInstance.levels.findIndex(
              (l) => Math.abs((l.height || 0) - height) < 40
            );
            if (idx >= 0) hlsInstance.currentLevel = idx;
          }
        }
        // Rebind encoder ladder when using server ABR (max_height).
        const video = document.getElementById("player-video");
        if (video && trackState.needsHls) {
          const resumeAt = (hlsOriginSec || 0) + (video.currentTime || 0);
          const statusEl = document.getElementById("player-status");
          const status = (m) => {
            if (statusEl) statusEl.textContent = m;
          };
          status("Applying ABR ladder…");
          await bindPlayback(activeDownloadId, video, status, {
            resumeAt,
            abr: true,
          });
        }
      } catch {
        /* ignore */
      }
    },
  });
  return cinema;
}

function setNerdOpen(open) {
  nerdOpen = !!open;
  const panel = document.getElementById("nerd-stats");
  panel?.classList.toggle("hidden", !nerdOpen);
  if (nerdOpen) refreshNerdStats();
}

function refreshNerdStats() {
  const body = document.getElementById("nerd-body");
  const video = document.getElementById("player-video");
  if (!body || !video || !nerdOpen) return;
  body.innerHTML = renderNerdStats({
    video,
    hls: hlsInstance,
    download: lastDownloadSnap,
    tracks: lastTracks,
    mode: playMode,
    downloadId: activeDownloadId,
    engineMetrics: lastEngineMetrics,
  });
}

export function stopPlayer() {
  clearTimers();
  destroyHls();
  if (streamWs) {
    try {
      streamWs.close();
    } catch {
      /* ignore */
    }
    streamWs = null;
  }
  if (cinema) {
    cinema.destroy();
    cinema = null;
  }
  activeDownloadId = null;
  lastTracks = null;
  lastDownloadSnap = null;
  lastEngineMetrics = null;
  playMode = "progressive";
  setNerdOpen(false);
  trackState = { audio: null, subtitle: null, needsHls: false };
  hlsOriginSec = 0;
  rawVttText = null;
}

async function waitUntilPlayable(downloadId, status, timeoutMs = 90000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    const res = await fetch(`/api/v1/downloads/${downloadId}`, {
      headers: { Accept: "application/json" },
    });
    if (!res.ok) {
      await new Promise((r) => setTimeout(r, 800));
      continue;
    }
    const st = await res.json();
    lastDownloadSnap = st;
    const peers = st.num_peers ?? 0;
    const pct = ((st.progress || 0) * 100).toFixed(1);
    status(
      `Peers ${peers} · ${pct}% · ${st.status}${
        st.name ? ` · ${st.name}` : ""
      }`
    );
    if (
      st.total_bytes > 0 &&
      (st.status === "streaming" ||
        st.status === "downloading" ||
        st.status === "completed" ||
        (st.progress || 0) > 0 ||
        peers > 0)
    ) {
      const probe = await fetch(`/api/v1/downloads/${downloadId}/media`, {
        method: "GET",
        headers: { Range: "bytes=0-1" },
      });
      if (probe.ok || probe.status === 206) {
        try {
          await probe.body?.cancel();
        } catch {
          /* ignore */
        }
        return st;
      }
      if (probe.status === 503) {
        status("Waiting for torrent metadata…");
      }
    }
    if (st.status === "error") {
      throw new Error(st.error || "Download failed");
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  throw new Error("Timed out waiting for stream — magnet may have no peers");
}

async function fetchTracks(downloadId) {
  const res = await fetch(`/api/v1/downloads/${downloadId}/tracks`);
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || "Could not probe tracks");
  }
  return res.json();
}

function wireOpenSubtitles(downloadId, video, status) {
  const searchBtn = document.getElementById("player-osubs-search");
  const sel = document.getElementById("player-osubs");
  const attachBtn = document.getElementById("player-osubs-attach");
  if (!searchBtn || !sel || !attachBtn) return;

  searchBtn.onclick = async () => {
    try {
      status("Searching OpenSubtitles…");
      const res = await fetch(
        `/api/v1/downloads/${downloadId}/subtitles/external`
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || "search failed");
      if (!data.configured) {
        status(
          data.error ||
            "Set TORRENTSEARCH_OPENSUBTITLES_KEY for online subtitles"
        );
        sel.innerHTML = "";
        return;
      }
      const rows = data.results || [];
      sel.innerHTML = rows
        .map(
          (r) =>
            `<option value="${r.file_id}">${escapeOpt(
              `${r.language || "?"} · ${r.release || r.id || r.file_id}`
            )}</option>`
        )
        .join("");
      status(
        rows.length
          ? `Found ${rows.length} OpenSubtitles result(s)`
          : "No OpenSubtitles matches"
      );
      const wrap = document.getElementById("player-tracks");
      wrap?.classList.remove("hidden");
    } catch (err) {
      status(err.message || String(err));
    }
  };

  attachBtn.onclick = async () => {
    const fileId = sel.value;
    if (!fileId) {
      status("Pick an OpenSubtitles result first");
      return;
    }
    try {
      status("Downloading subtitle…");
      const res = await fetch(
        `/api/v1/downloads/${downloadId}/subtitles/external?file_id=${encodeURIComponent(
          fileId
        )}`,
        { method: "POST" }
      );
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || data.error || "attach failed");
      await attachSubtitleTrack(video, data.subtitle_url);
      status("External subtitle attached");
    } catch (err) {
      status(err.message || String(err));
    }
  };
}

function fillTrackSelects(tracks, { onAudioChange, onSubChange }) {
  const audioSel = document.getElementById("player-audio");
  const subSel = document.getElementById("player-subs");
  const wrap = document.getElementById("player-tracks");
  if (!audioSel || !subSel || !wrap) return;

  const audios = tracks.audio || [];
  const subs = tracks.subtitles || [];

  audioSel.innerHTML = audios
    .map(
      (t, i) =>
        `<option value="${t.index}" ${
          t.default || i === 0 ? "selected" : ""
        }>${escapeOpt(t.label || `Audio ${t.index}`)}</option>`
    )
    .join("");

  subSel.innerHTML =
    `<option value="">Off</option>` +
    subs
      .map((t) => {
        const disabled = t.text_based === false ? " disabled" : "";
        const note = t.text_based === false ? " (bitmap — unsupported)" : "";
        return `<option value="${t.index}"${disabled}>${escapeOpt(
          (t.label || `Sub ${t.index}`) + note
        )}</option>`;
      })
      .join("");

  wrap.classList.toggle("hidden", audios.length + subs.length === 0);
  // Show panel when multi-audio or any subs
  if (audios.length > 1 || subs.length > 0) {
    wrap.classList.remove("hidden");
  }

  const defaultAudio =
    audios.find((t) => t.default)?.index ?? audios[0]?.index ?? null;
  trackState.audio = defaultAudio;
  trackState.subtitle = null;
  trackState.needsHls =
    !!tracks.needs_hls || audios.length > 1 || subs.length > 0;

  audioSel.onchange = () => {
    trackState.audio = audioSel.value === "" ? null : Number(audioSel.value);
    onAudioChange();
  };
  subSel.onchange = () => {
    trackState.subtitle = subSel.value === "" ? null : Number(subSel.value);
    onSubChange();
  };
}

function escapeOpt(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/"/g, "&quot;");
}

function fillFilePicker(downloadId, st, video, status) {
  const sel = document.getElementById("player-files");
  if (!sel) return;
  const files = Array.isArray(st.files) ? st.files : [];
  const mediaFiles = files.filter((f) =>
    /\.(mkv|mp4|avi|mov|m4v|webm|ts|m2ts|wmv)$/i.test(f.name || "")
  );
  if (mediaFiles.length <= 1) {
    sel.classList.add("hidden");
    sel.innerHTML = "";
    sel.onchange = null;
    return;
  }
  const cur = st.media_file_index ?? -1;
  sel.innerHTML = mediaFiles
    .map(
      (f) =>
        `<option value="${f.index}" ${
          f.index === cur ? "selected" : ""
        }>${escapeOpt(f.name || `File ${f.index}`)}</option>`
    )
    .join("");
  sel.classList.remove("hidden");
  sel.onchange = async () => {
    const index = Number(sel.value);
    if (!Number.isFinite(index)) return;
    status("Switching media file…");
    try {
      const res = await fetch(
        `/api/v1/downloads/${downloadId}/media-file?index=${index}`,
        { method: "POST" }
      );
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || data.error || "switch failed");
      trackState.needsHls = /\.(mkv|avi|ts|m2ts)$/i.test(
        mediaFiles.find((f) => f.index === index)?.name || ""
      );
      await bindPlayback(downloadId, video, status);
      status(`Playing file #${index}`);
    } catch (err) {
      status(err.message || String(err));
    }
  };
}

function clearSubtitleTracks(video) {
  if (subBlobUrl) {
    try {
      URL.revokeObjectURL(subBlobUrl);
    } catch {
      /* ignore */
    }
    subBlobUrl = null;
  }
  [...video.querySelectorAll("track")].forEach((t) => t.remove());
  try {
    for (const tt of video.textTracks || []) {
      tt.mode = "disabled";
    }
  } catch {
    /* ignore */
  }
}

async function attachSubtitleTrack(video, url) {
  clearSubtitleTracks(video);
  if (!url) {
    rawVttText = null;
    return;
  }
  const abs = url.startsWith("http") ? url : url;
  const res = await fetch(abs);
  if (!res.ok) throw new Error(`subtitle fetch ${res.status}`);
  let text = await res.text();
  rawVttText = text;
  if (!text.trim().startsWith("WEBVTT")) text = "WEBVTT\n\n" + text;
  text = text.replace(/<\/?b>/gi, "");
  text = shiftVtt(text, hlsOriginSec || 0);
  const blob = new Blob([text], { type: "text/vtt" });
  subBlobUrl = URL.createObjectURL(blob);

  video.crossOrigin = "anonymous";
  const track = document.createElement("track");
  track.kind = "subtitles";
  track.label = "Selected";
  track.srclang = "und";
  track.src = subBlobUrl;
  track.default = true;
  video.appendChild(track);

  const show = () => {
    try {
      for (const tt of video.textTracks) {
        tt.mode = "showing";
      }
    } catch {
      /* ignore */
    }
  };
  track.addEventListener("load", show);
  setTimeout(show, 300);
  setTimeout(show, 1200);
}

function hlsErrorDetail(data) {
  const d = data?.detail ?? data;
  if (typeof d === "string") return { error: d, retry: /retry/i.test(d) };
  if (d && typeof d === "object") {
    return {
      error: d.error || d.reason || JSON.stringify(d),
      retry: !!d.retry || d.reason === "sequential_gate",
      sequential_bytes: d.sequential_bytes,
      reason: d.reason,
    };
  }
  return { error: "ffmpeg HLS failed", retry: false };
}

async function startHlsSession(
  downloadId,
  { audio, startSeconds, burnSubtitle, audioOnly, abr, status } = {}
) {
  const params = new URLSearchParams();
  const a = audio !== undefined ? audio : trackState.audio;
  if (a != null) params.set("audio", String(a));
  if (startSeconds != null && Number.isFinite(startSeconds) && startSeconds > 0) {
    params.set("start_seconds", String(startSeconds));
  }
  if (burnSubtitle != null) params.set("burn_subtitle", String(burnSubtitle));
  if (audioOnly) params.set("audio_only", "true");
  if (abr) params.set("abr", "true");
  const qs = params.toString();
  const report = typeof status === "function" ? status : () => {};

  for (let attempt = 0; attempt < 48; attempt++) {
    const res = await fetch(
      `/api/v1/downloads/${downloadId}/hls${qs ? `?${qs}` : ""}`,
      { method: "POST" }
    );
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      cinema?.root?.classList.remove("is-buffering");
      return data;
    }
    const info = hlsErrorDetail(data);
    const retryable =
      res.status === 503 || info.retry || /retry|buffering|not ready/i.test(info.error);
    if (retryable && attempt < 47) {
      cinema?.root?.classList.add("is-buffering");
      const seq =
        info.sequential_bytes != null ? ` · ${Math.round(info.sequential_bytes / 1e6)} MB` : "";
      report(`Buffering torrent…${seq}`);
      cinema?.setLiveHint(true, "BUFFERING TORRENT…");
      const delay = Math.min(8000, 600 + attempt * 350);
      await new Promise((r) => setTimeout(r, delay));
      continue;
    }
    throw new Error(info.error || "ffmpeg HLS failed");
  }
  throw new Error("Timed out waiting for HLS buffer");
}

async function switchSubtitle(downloadId, index) {
  const q =
    index == null || index === ""
      ? ""
      : `?index=${encodeURIComponent(String(index))}`;
  const res = await fetch(`/api/v1/downloads/${downloadId}/subtitle${q}`, {
    method: "POST",
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || data.error || "Subtitle extract failed";
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function playWithHls(video, playlistUrl, status, { resumeAt, downloadId } = {}) {
  destroyHls();
  playMode = "hls";
  const Hls = window.Hls;
  const afterReady = () => {
    if (resumeAt != null && Number.isFinite(resumeAt) && resumeAt > 0) {
      try {
        video.currentTime = resumeAt;
      } catch {
        /* ignore */
      }
    }
    status("HLS ready — playing…");
    video.play().catch(() => status("Ready — press play"));
  };

  // Far scrub on incomplete HLS → restart ffmpeg with -ss
  let seekRestartBusy = false;
  const onSeeking = async () => {
    if (!downloadId || seekRestartBusy || playMode !== "hls") return;
    const target = video.currentTime;
    if (!Number.isFinite(target) || target < 0) return;
    let bufferedOk = false;
    try {
      for (let i = 0; i < video.buffered.length; i++) {
        if (target >= video.buffered.start(i) - 1 && target <= video.buffered.end(i) + 2) {
          bufferedOk = true;
          break;
        }
      }
    } catch {
      bufferedOk = true;
    }
    // Also treat jumps > 15s ahead of max buffered end as far seek
    let maxEnd = 0;
    try {
      for (let i = 0; i < video.buffered.length; i++) {
        maxEnd = Math.max(maxEnd, video.buffered.end(i));
      }
    } catch {
      /* ignore */
    }
    const farJump = maxEnd > 0 && target > maxEnd + 8;
    if (bufferedOk && !farJump) return;

    seekRestartBusy = true;
    status("Seeking (boost + restart HLS)…");
    cinema?.setLiveHint(true, "SEEKING…");
    try {
      // Pre-buffer: boost playhead before remux restart
      fetch(`/api/v1/downloads/${downloadId}/boost?duration=20&multiplier=3`, {
        method: "POST",
      }).catch(() => {});
      if (streamWs) {
        streamWs.cmd({
          cmd: "boost",
          download_id: downloadId,
          duration: 20,
          multiplier: 3,
        });
      }
      await new Promise((r) => setTimeout(r, 400));
      const absolute = (hlsOriginSec || 0) + target;
      hlsOriginSec = absolute;
      const hls = await startHlsSession(downloadId, {
        audio: trackState.audio,
        startSeconds: absolute,
        burnSubtitle: trackState.burnSubtitle ?? undefined,
        status,
      });
      playWithHls(video, hls.playlist_url, status, {
        resumeAt: 0,
        downloadId,
      });
      if (trackState.subtitle != null && trackState.burnSubtitle == null) {
        try {
          const sub = await switchSubtitle(downloadId, trackState.subtitle);
          await attachSubtitleTrack(video, sub.subtitle_url || null);
        } catch {
          /* ignore */
        }
      }
    } catch (err) {
      status(err.message || String(err));
      seekRestartBusy = false;
    }
  };
  video.addEventListener("seeking", onSeeking);
  hlsSeekHandler = onSeeking;
  hlsSeekVideo = video;

  if (Hls && Hls.isSupported()) {
    hlsInstance = new Hls({
      enableWorker: true,
      lowLatencyMode: false,
      maxBufferLength: 60,
      maxMaxBufferLength: 120,
      backBufferLength: 30,
      maxBufferHole: 0.5,
      startPosition: resumeAt != null && resumeAt > 0 ? resumeAt : -1,
    });
    hlsInstance.loadSource(playlistUrl);
    hlsInstance.attachMedia(video);
    hlsInstance.on(Hls.Events.MANIFEST_PARSED, afterReady);
    hlsInstance.on(Hls.Events.ERROR, async (_e, data) => {
      if (!data?.fatal) return;
      const retryable =
        data.type === Hls.ErrorTypes.NETWORK_ERROR ||
        data.details === "manifestLoadError" ||
        data.details === "levelLoadError" ||
        data.details === "fragLoadError";
      if (retryable && downloadId) {
        status("HLS stalled — retrying (buffering torrent)…");
        cinema?.root?.classList.add("is-buffering");
        cinema?.setLiveHint(true, "BUFFERING TORRENT…");
        try {
          await new Promise((r) => setTimeout(r, 1200));
          const absolute = (hlsOriginSec || 0) + (video.currentTime || 0);
          hlsOriginSec = absolute;
          const hls = await startHlsSession(downloadId, {
            audio: trackState.audio,
            startSeconds: absolute > 0 ? absolute : undefined,
            burnSubtitle: trackState.burnSubtitle ?? undefined,
            status,
          });
          playWithHls(video, hls.playlist_url, status, {
            resumeAt: 0,
            downloadId,
          });
          return;
        } catch (err) {
          status(err.message || String(err));
          return;
        }
      }
      status(`HLS error: ${data.type} / ${data.details}`);
    });
    return;
  }
  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = playlistUrl;
    video.addEventListener("loadedmetadata", afterReady, { once: true });
    return;
  }
  throw new Error("HLS not supported in this browser (need hls.js)");
}

async function playDirect(video, downloadId, status) {
  destroyHls();
  playMode = "progressive";
  const mediaUrl = `/api/v1/downloads/${downloadId}/media`;
  video.controls = false;
  video.playsInline = true;
  video.src = mediaUrl;
  video.load();
  status("Playing direct stream…");
  try {
    await video.play();
  } catch {
    status("Ready — press play");
  }
}

async function bindPlayback(downloadId, video, status, { resumeAt, abr } = {}) {
  // MP4/MOV: prefer progressive Range — early HLS is weak without moov.
  const name = lastDownloadSnap?.file_name || lastDownloadSnap?.name || "";
  const seekHeavy = /\.(mp4|m4v|mov)$/i.test(name);
  if (seekHeavy && !trackState.burnSubtitle && !(lastTracks?.audio?.length > 1)) {
    trackState.needsHls = false;
  }

  if (trackState.needsHls) {
    // resumeAt is absolute media time (ffmpeg -ss). Player clock resets to 0.
    const absolute =
      resumeAt != null && Number.isFinite(resumeAt) && resumeAt > 0
        ? resumeAt
        : 0;
    hlsOriginSec = absolute;
    status("Starting ffmpeg (audio → HLS)…");
    const hls = await startHlsSession(downloadId, {
      audio: trackState.audio,
      startSeconds: absolute > 0 ? absolute : undefined,
      burnSubtitle: trackState.burnSubtitle ?? undefined,
      abr: !!abr,
      status,
    });
    playWithHls(video, hls.playlist_url, status, { resumeAt: 0, downloadId });
    // Softsubs only when not burning in
    if (trackState.subtitle != null && trackState.burnSubtitle == null) {
      try {
        const sub = await switchSubtitle(downloadId, trackState.subtitle);
        await attachSubtitleTrack(video, sub.subtitle_url || null);
      } catch (err) {
        status(`Subs: ${err.message}`);
      }
    } else {
      clearSubtitleTracks(video);
    }
  } else {
    hlsOriginSec = 0;
    await playDirect(video, downloadId, status);
    clearSubtitleTracks(video);
  }
  cinema?.setLiveHint(true);
  cinema?.showChrome();
}

async function onAudioTrackChange(downloadId, video, status) {
  // video.currentTime is relative to hlsOriginSec after a far seek
  const resumeAt = (hlsOriginSec || 0) + (video.currentTime || 0);
  status("Switching audio (keeping position)…");
  trackState.needsHls = true;
  await bindPlayback(downloadId, video, status, { resumeAt });
}

async function onSubTrackChange(downloadId, video, status) {
  if (trackState.subtitle == null) {
    clearSubtitleTracks(video);
    trackState.burnSubtitle = null;
    status("Subtitles off");
    try {
      await switchSubtitle(downloadId, null);
    } catch {
      /* ignore */
    }
    // If we were burning in, restart HLS without burn
    if (trackState.needsHls) {
      const resumeAt = (hlsOriginSec || 0) + (video.currentTime || 0);
      await bindPlayback(downloadId, video, status, { resumeAt });
    }
    return;
  }

  const subMeta = (lastTracks?.subtitles || []).find(
    (t) => t.index === trackState.subtitle
  );
  const bitmap = subMeta && subMeta.text_based === false;

  if (bitmap) {
    status("Burning in hardsub (PGS/VobSub)…");
    trackState.burnSubtitle = trackState.subtitle;
    trackState.needsHls = true;
    const resumeAt = (hlsOriginSec || 0) + (video.currentTime || 0);
    clearSubtitleTracks(video);
    await bindPlayback(downloadId, video, status, { resumeAt });
    status("Hardsubs burned into video");
    return;
  }

  const wasBurning = trackState.burnSubtitle != null;
  trackState.burnSubtitle = null;
  status("Loading subtitle…");
  try {
    if (wasBurning) {
      const resumeAt = (hlsOriginSec || 0) + (video.currentTime || 0);
      await bindPlayback(downloadId, video, status, { resumeAt });
    }
    const sub = await switchSubtitle(downloadId, trackState.subtitle);
    if (!sub.ready || !sub.subtitle_url) {
      throw new Error(
        sub.error || "Subtitle not ready"
      );
    }
    await attachSubtitleTrack(video, sub.subtitle_url);
    status("Subtitles on");
  } catch (err) {
    status(err.message || String(err));
  }
}

/**
 * Play a server-managed download id in a <video> element.
 */
export async function playDownloadId({
  downloadId,
  video,
  status = () => {},
  onReady = () => {},
}) {
  clearTimers();
  destroyHls();
  activeDownloadId = downloadId;

  ensureCinema(video);

  try {
    video.pause();
    video.removeAttribute("src");
    [...video.querySelectorAll("track")].forEach((t) => t.remove());
    video.load();
  } catch {
    /* ignore */
  }

  status("Starting server torrent engine…");
  const st = await waitUntilPlayable(downloadId, status);
  lastDownloadSnap = st;

  const titleEl = document.getElementById("cinema-title");
  if (titleEl) titleEl.textContent = st.name || "Stream";

  // Live metrics + commands over WebSocket
  if (streamWs) {
    try {
      streamWs.close();
    } catch {
      /* ignore */
    }
  }
  streamWs = connectStreamWs({
    onMetrics: (data) => {
      lastEngineMetrics = data?.engine || data;
    },
    onStatus: (id, data) => {
      if (id === downloadId) lastDownloadSnap = data;
    },
    onBuffer: (id, buf) => {
      if (id !== downloadId || !buf) return;
      if (buf.underrun) {
        cinema?.root?.classList.add("is-buffering");
        cinema?.setLiveHint(true, "UNDERRUN — Boost?");
      } else if (buf.warning) {
        cinema?.setLiveHint(true, String(buf.warning).toUpperCase());
      } else {
        cinema?.root?.classList.remove("is-buffering");
        cinema?.setLiveHint(true, "LIVE BUFFER");
      }
    },
  });
  streamWs.subscribe(downloadId);

  fillFilePicker(downloadId, st, video, status);

  // Continue-watching
  let resumeHint = 0;
  try {
    const rp = await fetch(
      `/api/v1/downloads/${downloadId}/resume-position?file_index=${
        st.media_file_index >= 0 ? st.media_file_index : 0
      }`
    );
    const rj = await rp.json();
    if (rj.found && rj.position_sec > 5) resumeHint = rj.position_sec;
  } catch {
    /* ignore */
  }

  let tracks = null;
  try {
    status("Probing tracks with ffprobe…");
    tracks = await fetchTracks(downloadId);
    lastTracks = tracks;
    fillTrackSelects(tracks, {
      onAudioChange: async () => {
        try {
          await onAudioTrackChange(downloadId, video, status);
        } catch (err) {
          status(err.message || String(err));
        }
      },
      onSubChange: async () => {
        try {
          await onSubTrackChange(downloadId, video, status);
        } catch (err) {
          status(err.message || String(err));
        }
      },
    });
    wireOpenSubtitles(downloadId, video, status);
  } catch (err) {
    trackState.needsHls = /\.mkv$/i.test(st.name || "");
    status(`Track probe skipped: ${err.message}`);
    wireOpenSubtitles(downloadId, video, status);
  }

  await bindPlayback(downloadId, video, status, {
    resumeAt: resumeHint > 0 ? resumeHint : undefined,
  });
  if (resumeHint > 0 && !trackState.needsHls) {
    try {
      video.currentTime = resumeHint;
    } catch {
      /* ignore */
    }
  }
  status(`Playing “${st.name || downloadId}”…`);
  document.getElementById("cinema")?.focus({ preventScroll: true });

  // Persist resume position periodically
  const saveResume = () => {
    const t = (hlsOriginSec || 0) + (video.currentTime || 0);
    if (!Number.isFinite(t) || t < 1) return;
    fetch(`/api/v1/downloads/${downloadId}/resume-position`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        file_index: st.media_file_index >= 0 ? st.media_file_index : 0,
        position_sec: t,
        duration_sec: video.duration || null,
        title: st.name,
      }),
    }).catch(() => {});
  };
  setInterval(saveResume, 8000);
  video.addEventListener("pause", saveResume);

  // Waveform scrub (API-backed; non-fatal)
  fetch(`/api/v1/downloads/${downloadId}/waveform`, { method: "POST" })
    .then((r) => r.json())
    .then(async (meta) => {
      const url = meta.waveform_url;
      if (!url) return;
      const wr = await fetch(url);
      const wj = await wr.json();
      const peaks = wj.data || wj.samples || wj;
      if (Array.isArray(peaks)) cinema?.drawWaveform(peaks.flat?.() || peaks);
    })
    .catch(() => {});

  // Scene chapters → scrub markers
  fetch(`/api/v1/downloads/${downloadId}/chapters`, { method: "POST" })
    .then((r) => r.json())
    .then((meta) => {
      const ch = meta.chapters || meta.scenes || meta;
      if (Array.isArray(ch)) cinema?.setChapters(ch);
    })
    .catch(() => {});

  cinema?.setAbrVisible(true);

  statsTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/v1/downloads/${downloadId}`);
      if (!res.ok) return;
      const cur = await res.json();
      lastDownloadSnap = cur;
      const kb = ((cur.download_rate || 0) / 1024).toFixed(0);
      let line = `Peers ${cur.num_peers || 0} · ${((cur.progress || 0) * 100).toFixed(
        1
      )}% · ${kb} KB/s`;
      const bufRes = await fetch(`/api/v1/downloads/${downloadId}/buffer`);
      if (bufRes.ok) {
        const buf = await bufRes.json();
        if (buf?.underrun) {
          line += " · UNDERRUN";
          cinema?.setLiveHint(true, "UNDERRUN — Boost?");
        } else if (buf?.warning) {
          cinema?.setLiveHint(true, String(buf.warning).toUpperCase());
        } else {
          cinema?.setLiveHint(true, "LIVE BUFFER");
        }
      } else {
        cinema?.setLiveHint(true, "LIVE BUFFER");
      }
      status(line);
      if (video.duration && Number.isFinite(video.duration) && cur.total_bytes) {
        const offset = Math.floor(
          (video.currentTime / video.duration) * cur.total_bytes
        );
        fetch(`/api/v1/downloads/${downloadId}/playhead`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ offset_bytes: offset }),
        }).catch(() => {});
      }
    } catch {
      /* ignore */
    }
  }, 1500);

  nerdTimer = setInterval(() => refreshNerdStats(), 500);

  onReady({
    name: st.name,
    length: st.total_bytes,
    playable: true,
    tracks,
  });

  return { downloadId, state: st, tracks };
}

/** @deprecated browser WebTorrent path removed */
export async function playMagnet() {
  throw new Error(
    "Browser torrent streaming was removed. Use server stream + ffmpeg HLS for MKV tracks."
  );
}

export function switchFile() {
  /* torrent multi-file switch not yet wired */
}

export async function warmStreamEngine(status = () => {}) {
  try {
    const res = await fetch("/api/v1/downloads/presets");
    status(
      res.ok ? "Stream engine available" : "API not ready",
      res.ok ? "good" : "warn"
    );
    return res.ok;
  } catch {
    status("API offline", "bad");
    return false;
  }
}
