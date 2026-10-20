/** API client for TorrentSearch */

const API_BASE = "";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: {
      Accept: "application/json",
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...options.headers,
    },
    ...options,
  });
  const text = await res.text();
  let data = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!res.ok) {
    const detail =
      (data && (data.detail || data.message)) ||
      (typeof data === "string" ? data : res.statusText);
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

export const api = {
  health: () => request("/health"),
  activity: (limit = 50) => request(`/api/v1/activity?limit=${limit}`),
  providers: () => request("/api/v1/providers"),
  search: (body) =>
    request("/api/v1/search", { method: "POST", body: JSON.stringify(body) }),
  searches: (limit = 40) => request(`/api/v1/searches?limit=${limit}`),
  getSearch: (id) => request(`/api/v1/searches/${id}`),
  deleteSearch: (id) => request(`/api/v1/searches/${id}`, { method: "DELETE" }),
  resolveMagnet: (dbId) =>
    request(`/api/v1/torrents/${dbId}/resolve-magnet`, { method: "POST" }),
  presets: () => request("/api/v1/downloads/presets"),
  downloads: () => request("/api/v1/downloads"),
  createDownload: (body) =>
    request("/api/v1/downloads", { method: "POST", body: JSON.stringify(body) }),
  pause: (id) => request(`/api/v1/downloads/${id}/pause`, { method: "POST" }),
  resume: (id) => request(`/api/v1/downloads/${id}/resume`, { method: "POST" }),
  playhead: (id, body) =>
    request(`/api/v1/downloads/${id}/playhead`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  removeDownload: (id) =>
    request(`/api/v1/downloads/${id}`, { method: "DELETE" }),
  tracks: (id) => request(`/api/v1/downloads/${id}/tracks`),
  startHls: (id, { audio, subtitle } = {}) => {
    const q = new URLSearchParams();
    if (audio != null) q.set("audio", String(audio));
    if (subtitle != null) q.set("subtitle", String(subtitle));
    const qs = q.toString();
    return request(`/api/v1/downloads/${id}/hls${qs ? `?${qs}` : ""}`, {
      method: "POST",
    });
  },
  setSubtitle: (id, index) => {
    const q =
      index == null || index === ""
        ? ""
        : `?index=${encodeURIComponent(String(index))}`;
    return request(`/api/v1/downloads/${id}/subtitle${q}`, { method: "POST" });
  },
};
