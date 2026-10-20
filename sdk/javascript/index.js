/**
 * torrent-engine SDK — Node 18+ / browsers (fetch).
 * Point baseUrl at a running sidecar (default http://127.0.0.1:17890).
 */

export const SDK_VERSION = "0.1.0";

function cmpVersion(a, b) {
  const parts = (s) =>
    String(s)
      .replace(/-/g, ".")
      .split(".")
      .map((x) => parseInt(x, 10) || 0);
  const pa = parts(a);
  const pb = parts(b);
  const n = Math.max(pa.length, pb.length, 3);
  for (let i = 0; i < n; i++) {
    const va = pa[i] || 0;
    const vb = pb[i] || 0;
    if (va < vb) return -1;
    if (va > vb) return 1;
  }
  return 0;
}

export class EngineError extends Error {
  /**
   * @param {number} status
   * @param {any} data
   */
  constructor(status, data) {
    const msg =
      (data && (data.error || data.detail)) ||
      (typeof data === "string" ? data : JSON.stringify(data));
    super(`HTTP ${status}: ${msg}`);
    this.name = "EngineError";
    this.status = status;
    this.data = data;
  }
}

export class TorrentEngine {
  /**
   * @param {string} [baseUrl]
   * @param {{ fetch?: typeof fetch, headers?: Record<string,string> }} [opts]
   */
  constructor(baseUrl = "http://127.0.0.1:17890", opts = {}) {
    this.baseUrl = String(baseUrl).replace(/\/$/, "");
    this._fetch = opts.fetch || globalThis.fetch.bind(globalThis);
    this._headers = opts.headers || {};
  }

  /**
   * @param {string} method
   * @param {string} path
   * @param {{ body?: any, query?: Record<string, any> }} [opts]
   */
  async request(method, path, opts = {}) {
    let url = `${this.baseUrl}${path}`;
    if (opts.query) {
      const q = new URLSearchParams();
      for (const [k, v] of Object.entries(opts.query)) {
        if (v != null) q.set(k, String(v));
      }
      const s = q.toString();
      if (s) url += (url.includes("?") ? "&" : "?") + s;
    }
    /** @type {RequestInit} */
    const init = {
      method,
      headers: { ...this._headers },
    };
    if (opts.body !== undefined) {
      init.headers = {
        "Content-Type": "application/json",
        ...init.headers,
      };
      init.body = JSON.stringify(opts.body);
    }
    const res = await this._fetch(url, init);
    const ct = res.headers.get("content-type") || "";
    const data = ct.includes("application/json")
      ? await res.json().catch(() => ({}))
      : await res.text();
    if (!res.ok) throw new EngineError(res.status, data);
    return data;
  }

  health() {
    return this.request("GET", "/v1/health");
  }

  version() {
    return this.request("GET", "/v1/version");
  }

  /** Ask the running engine to check its update manifest. */
  checkEngineUpdate() {
    return this.request("GET", "/v1/update/check");
  }

  /**
   * Fetch the shared update manifest and compare this JS SDK version.
   * @param {{ manifestUrl?: string }} [opts]
   */
  async checkSdkUpdate(opts = {}) {
    const url = (
      opts.manifestUrl ||
      (typeof process !== "undefined" &&
        process.env &&
        process.env.TORRENT_ENGINE_UPDATE_URL) ||
      ""
    ).trim();
    const out = {
      sdk: "javascript",
      current_version: SDK_VERSION,
      ok: false,
      update_available: false,
    };
    if (!url) {
      out.error =
        "Set TORRENT_ENGINE_UPDATE_URL or pass { manifestUrl }";
      return out;
    }
    const res = await this._fetch(url);
    if (!res.ok) {
      out.error = `HTTP ${res.status}`;
      return out;
    }
    const data = await res.json();
    const latest = data?.sdk?.javascript || data?.version || "";
    out.ok = true;
    out.latest_version = latest;
    out.manifest = data;
    out.update_available = !!latest && cmpVersion(SDK_VERSION, latest) < 0;
    out.notes = data?.notes;
    return out;
  }

  session() {
    return this.request("GET", "/v1/session");
  }

  metricsJson() {
    return this.request("GET", "/v1/metrics.json");
  }

  /**
   * @param {string} magnetOrUrl
   * @param {{ mode?: string, id?: string, peers?: object[], selective_files?: number[] }} [opts]
   */
  add(magnetOrUrl, opts = {}) {
    return this.request("POST", "/v1/torrents", {
      body: {
        magnet: magnetOrUrl,
        mode: opts.mode || "stream",
        ...(opts.id ? { id: opts.id } : {}),
        ...(opts.peers ? { peers: opts.peers } : {}),
        ...(opts.selective_files
          ? { selective_files: opts.selective_files }
          : {}),
      },
    });
  }

  list() {
    return this.request("GET", "/v1/torrents");
  }

  /** @param {string} id */
  status(id) {
    return this.request("GET", `/v1/torrents/${encodeURIComponent(id)}`);
  }

  /** @param {string} id */
  pause(id) {
    return this.request("POST", `/v1/torrents/${encodeURIComponent(id)}/pause`);
  }

  /** @param {string} id */
  resume(id) {
    return this.request("POST", `/v1/torrents/${encodeURIComponent(id)}/resume`);
  }

  /**
   * @param {string} id
   * @param {{ deleteFiles?: boolean }} [opts]
   */
  remove(id, opts = {}) {
    const q = opts.deleteFiles ? "?delete_files=true" : "?delete_files=false";
    return this.request(
      "DELETE",
      `/v1/torrents/${encodeURIComponent(id)}${q}`
    );
  }

  /**
   * @param {string} id
   * @param {number} offsetBytes
   */
  playhead(id, offsetBytes) {
    return this.request(
      "POST",
      `/v1/torrents/${encodeURIComponent(id)}/playhead`,
      { body: { offset_bytes: offsetBytes } }
    );
  }

  /** @param {string} id */
  buffer(id) {
    return this.request("GET", `/v1/torrents/${encodeURIComponent(id)}/buffer`);
  }

  /**
   * @param {string} id
   * @param {{ duration?: number, multiplier?: number }} [opts]
   */
  boost(id, opts = {}) {
    return this.request(
      "POST",
      `/v1/torrents/${encodeURIComponent(id)}/boost`,
      {
        body: {
          duration: opts.duration ?? 25,
          multiplier: opts.multiplier ?? 2.5,
        },
      }
    );
  }

  /** @param {string} id */
  tracks(id) {
    return this.request("GET", `/v1/torrents/${encodeURIComponent(id)}/tracks`);
  }

  /**
   * May throw EngineError 503 with data.retry until sequential gate passes.
   * @param {string} id
   * @param {Record<string, any>} [params]
   */
  startHls(id, params = {}) {
    return this.request(
      "POST",
      `/v1/torrents/${encodeURIComponent(id)}/hls`,
      { body: params, query: params }
    );
  }

  /**
   * @param {string} id
   * @param {number} index
   */
  setMediaFile(id, index) {
    return this.request(
      "POST",
      `/v1/torrents/${encodeURIComponent(id)}/media-file`,
      { body: { index } }
    );
  }

  /** @param {string} id */
  mediaUrl(id) {
    return `${this.baseUrl}/v1/torrents/${encodeURIComponent(id)}/media`;
  }

  /**
   * @param {string} id
   * @param {{ master?: boolean }} [opts]
   */
  hlsPlaylistUrl(id, opts = {}) {
    const name = opts.master ? "master.m3u8" : "index.m3u8";
    return `${this.baseUrl}/v1/torrents/${encodeURIComponent(id)}/hls/${name}`;
  }
}

export default TorrentEngine;
