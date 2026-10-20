/**
 * Server-side WebTorrent worker.
 * Supports UDP/HTTP trackers (unlike browser WebTorrent).
 *
 * Endpoints:
 *   GET  /health
 *   POST /add          { id, magnet }
 *   GET  /status/:id
 *   GET  /media/:id    (Range-capable video/audio bytes)
 *   DELETE /remove/:id
 *
 * Usage: node server.js --port 8766
 */

"use strict";

const http = require("http");
const { URL } = require("url");
const WebTorrent = require("webtorrent");

const VIDEO_RE = /\.(mp4|webm|m4v|mov|mkv|avi|ts|m2ts|ogg|ogv)$/i;

function argPort() {
  const i = process.argv.indexOf("--port");
  if (i >= 0 && process.argv[i + 1]) return Number(process.argv[i + 1]);
  return Number(process.env.WT_PORT || 0); // 0 = ephemeral
}

const client = new WebTorrent({ maxConns: 55 });
/** @type {Map<string, { torrent: any, file: any, magnet: string, started: number }>} */
const jobs = new Map();

client.on("error", (err) => {
  console.error("[wt] client error", err.message || err);
});

function pickFile(torrent) {
  const videos = torrent.files.filter((f) => VIDEO_RE.test(f.name));
  const pool = videos.length ? videos : torrent.files;
  if (!pool.length) return null;
  return pool.reduce((a, b) => (a.length >= b.length ? a : b));
}

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      try {
        const raw = Buffer.concat(chunks).toString("utf8") || "{}";
        resolve(JSON.parse(raw));
      } catch (err) {
        reject(err);
      }
    });
    req.on("error", reject);
  });
}

function sendJson(res, code, obj) {
  const body = JSON.stringify(obj);
  res.writeHead(code, {
    "Content-Type": "application/json",
    "Content-Length": Buffer.byteLength(body),
    "Access-Control-Allow-Origin": "*",
  });
  res.end(body);
}

function jobStatus(id, job) {
  const t = job.torrent;
  const f = job.file;
  return {
    id,
    magnet: job.magnet,
    name: f ? f.name : t && t.name,
    infoHash: t && t.infoHash,
    ready: !!(t && t.ready),
    progress: t ? t.progress : 0,
    downloadSpeed: t ? t.downloadSpeed : 0,
    uploadSpeed: t ? t.uploadSpeed : 0,
    numPeers: t ? t.numPeers : 0,
    totalBytes: f ? f.length : t ? t.length : 0,
    downloadedBytes: f
      ? Math.floor((f.progress || 0) * f.length)
      : t
        ? t.downloaded
        : 0,
    fileName: f ? f.name : null,
    files: t
      ? t.files.map((x) => ({ name: x.name, length: x.length }))
      : [],
    done: !!(t && t.done),
  };
}

function mimeFor(name) {
  const n = (name || "").toLowerCase();
  if (n.endsWith(".mp4") || n.endsWith(".m4v")) return "video/mp4";
  if (n.endsWith(".webm")) return "video/webm";
  if (n.endsWith(".mkv")) return "video/x-matroska";
  if (n.endsWith(".mov")) return "video/quicktime";
  if (n.endsWith(".avi")) return "video/x-msvideo";
  if (n.endsWith(".ogg") || n.endsWith(".ogv")) return "video/ogg";
  return "application/octet-stream";
}

function serveMedia(req, res, job) {
  const file = job.file;
  if (!file) {
    sendJson(res, 404, { error: "no file selected yet" });
    return;
  }

  const total = file.length;
  const mime = mimeFor(file.name);
  const range = req.headers.range;

  res.setHeader("Accept-Ranges", "bytes");
  res.setHeader("Content-Type", mime);
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Cache-Control", "no-store");

  let start = 0;
  let end = total - 1;

  if (range) {
    const m = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (m) {
      if (m[1] !== "") start = parseInt(m[1], 10);
      if (m[2] !== "") end = parseInt(m[2], 10);
      if (Number.isNaN(start) || Number.isNaN(end) || start > end || end >= total) {
        res.writeHead(416, {
          "Content-Range": `bytes */${total}`,
        });
        res.end();
        return;
      }
      res.writeHead(206, {
        "Content-Range": `bytes ${start}-${end}/${total}`,
        "Content-Length": end - start + 1,
      });
    } else {
      res.writeHead(200, { "Content-Length": total });
    }
  } else {
    res.writeHead(200, { "Content-Length": total });
  }

  // Critical for streaming: prioritize pieces near the requested range.
  try {
    if (typeof file.select === "function") file.select();
  } catch (_) {
    /* ignore */
  }

  const stream = file.createReadStream({ start, end });
  stream.on("error", (err) => {
    console.error("[wt] stream error", err.message || err);
    if (!res.headersSent) sendJson(res, 500, { error: String(err.message || err) });
    else res.destroy(err);
  });
  req.on("close", () => {
    try {
      stream.destroy();
    } catch (_) {
      /* ignore */
    }
  });
  stream.pipe(res);
}

function existingFile(torrent) {
  for (const j of jobs.values()) {
    if (j.torrent === torrent && j.file) return j.file;
  }
  return null;
}

function selectMedia(torrent) {
  if (!torrent || !torrent.ready) return null;
  const already = existingFile(torrent);
  if (already) return already;
  torrent.files.forEach((f) => {
    try {
      f.deselect();
    } catch (_) {
      /* ignore */
    }
  });
  const file = pickFile(torrent);
  if (file) {
    try {
      file.select();
    } catch (_) {
      /* ignore */
    }
  }
  return file;
}

function waitReady(torrent, ms = 45000) {
  return new Promise((resolve, reject) => {
    if (torrent.ready) {
      resolve();
      return;
    }
    let settled = false;
    const finish = (fn, arg) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn(arg);
    };
    const onReady = () => finish(resolve);
    const onError = (err) => finish(reject, err);
    const cleanup = () => {
      torrent.removeListener("ready", onReady);
      torrent.removeListener("error", onError);
    };
    torrent.once("ready", onReady);
    torrent.once("error", onError);
    setTimeout(() => finish(resolve), ms);
  });
}

async function handleAdd(body) {
  const id = String(body.id || "").trim();
  const magnet = String(body.magnet || "").trim();
  if (!id || !magnet.startsWith("magnet:")) {
    const err = new Error("id and magnet required");
    err.status = 400;
    throw err;
  }
  if (jobs.has(id)) {
    return jobStatus(id, jobs.get(id));
  }

  // WebTorrent allows only one torrent per infoHash — reuse if already loaded.
  let torrent = null;
  try {
    torrent = client.get(magnet);
  } catch (_) {
    torrent = null;
  }

  if (!torrent) {
    torrent = client.add(magnet, {
      announce: [
        "udp://tracker.opentrackr.org:1337/announce",
        "udp://open.stealth.si:80/announce",
        "udp://tracker.torrent.eu.org:451/announce",
        "udp://exodus.desync.com:6969/announce",
        "udp://tracker.moeking.me:6969/announce",
        "http://tracker.openbittorrent.com:80/announce",
        "wss://tracker.openwebtorrent.com",
        "wss://tracker.webtorrent.dev",
      ],
    });
  }

  const job = { torrent, file: null, magnet, started: Date.now() };
  jobs.set(id, job);

  torrent.on("error", (err) => {
    console.error("[wt] torrent error", id, err.message || err);
  });

  try {
    await waitReady(torrent);
  } catch (err) {
    const msg = String(err && err.message ? err.message : err);
    if (/duplicate torrent/i.test(msg)) {
      const survivor = client.get(magnet);
      if (survivor) {
        job.torrent = survivor;
        torrent = survivor;
      } else {
        jobs.delete(id);
        throw err;
      }
    } else {
      jobs.delete(id);
      throw err;
    }
  }

  job.file = selectMedia(torrent);
  return jobStatus(id, job);
}

function handleRemove(id) {
  const job = jobs.get(id);
  if (!job) return false;
  jobs.delete(id);
  const shared = [...jobs.values()].some((j) => j.torrent === job.torrent);
  if (!shared) {
    try {
      job.torrent.destroy();
    } catch (_) {
      /* ignore */
    }
  }
  return true;
}

const server = http.createServer(async (req, res) => {
  if (req.method === "OPTIONS") {
    res.writeHead(204, {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET,POST,DELETE,OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type,Range",
    });
    res.end();
    return;
  }

  const u = new URL(req.url, "http://127.0.0.1");
  try {
    if (req.method === "GET" && u.pathname === "/health") {
      sendJson(res, 200, { ok: true, torrents: jobs.size });
      return;
    }

    if (req.method === "POST" && u.pathname === "/add") {
      const body = await readBody(req);
      const st = await handleAdd(body);
      sendJson(res, 200, st);
      return;
    }

    if (req.method === "GET" && u.pathname.startsWith("/status/")) {
      const id = decodeURIComponent(u.pathname.slice("/status/".length));
      const job = jobs.get(id);
      if (!job) {
        sendJson(res, 404, { error: "not found" });
        return;
      }
      sendJson(res, 200, jobStatus(id, job));
      return;
    }

    if (req.method === "GET" && u.pathname.startsWith("/media/")) {
      const id = decodeURIComponent(u.pathname.slice("/media/".length));
      const job = jobs.get(id);
      if (!job) {
        sendJson(res, 404, { error: "not found" });
        return;
      }
      if (!job.torrent.ready || !job.file) {
        // Wait briefly for metadata if just added
        sendJson(res, 503, { error: "torrent not ready", retry: true });
        return;
      }
      serveMedia(req, res, job);
      return;
    }

    if (req.method === "DELETE" && u.pathname.startsWith("/remove/")) {
      const id = decodeURIComponent(u.pathname.slice("/remove/".length));
      sendJson(res, 200, { deleted: handleRemove(id) });
      return;
    }

    sendJson(res, 404, { error: "not found" });
  } catch (err) {
    console.error("[wt] handler", err);
    sendJson(res, err.status || 500, { error: String(err.message || err) });
  }
});

const port = argPort();
server.listen(port, "127.0.0.1", () => {
  const addr = server.address();
  // Parent process parses this line.
  console.log(`WT_READY port=${addr.port}`);
});

function shutdown() {
  for (const id of [...jobs.keys()]) handleRemove(id);
  try {
    client.destroy();
  } catch (_) {
    /* ignore */
  }
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}

process.on("SIGINT", shutdown);
process.on("SIGTERM", shutdown);
