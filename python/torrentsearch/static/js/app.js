import { api } from "./api.js";
import {
  DEMO_MAGNET,
  playDownloadId,
  stopPlayer,
  warmStreamEngine,
} from "./player.js";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

let toastTimer = null;
let providersCache = [];
let defaultProviderIds = new Set();
let lastResults = [];
let activeTab = "search";
let streamEngineReady = false;

function toast(message, ms = 3200) {
  const el = $("#toast");
  el.textContent = message;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), ms);
}

function fmtBytes(n) {
  if (n == null || Number.isNaN(n)) return "—";
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  let v = Number(n);
  while (v >= 1024 && i < u.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(i ? 1 : 0)} ${u[i]}`;
}

function escapeHtml(s) {
  return String(s ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function animateIn(selector, opts = {}) {
  const nodes = $$(selector);
  if (!nodes.length) return;
  if (typeof anime === "undefined") {
    nodes.forEach((el) => {
      el.style.opacity = "1";
      el.style.transform = "none";
    });
    return;
  }
  anime({
    targets: nodes,
    opacity: [0, 1],
    translateY: opts.x ? 0 : [12, 0],
    translateX: opts.x ? [-8, 0] : 0,
    delay: anime.stagger(opts.stagger ?? 40),
    duration: opts.duration ?? 420,
    easing: "easeOutCubic",
  });
}

function switchTab(tab) {
  activeTab = tab;
  $$(".tab-btn").forEach((b) =>
    b.classList.toggle("tab-active", b.dataset.tab === tab)
  );
  $$(".panel").forEach((p) => p.classList.add("hidden"));
  $(`#panel-${tab}`).classList.remove("hidden");
  if (tab === "library") refreshLibrary();
  if (tab === "downloads") refreshDownloads();
  if (tab === "history") refreshHistory();
  if (tab === "activity") refreshActivity();
}

function fmtSec(sec) {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

async function refreshLibrary() {
  const cont = $("#library-continue");
  const empty = $("#library-continue-empty");
  const dls = $("#library-downloads");
  if (!cont || !dls) return;
  try {
    const [cw, lib] = await Promise.all([
      fetch("/api/v1/library/continue").then((r) => r.json()),
      fetch("/api/v1/library/downloads").then((r) => r.json()),
    ]);
    const items = cw.items || [];
    empty?.classList.toggle("hidden", items.length > 0);
    cont.innerHTML = items
      .map(
        (it) => `
      <article class="rounded-xl border border-ink-700 bg-ink-900/50 p-4 flex flex-col gap-2">
        <h4 class="font-medium text-sm leading-snug line-clamp-2">${escapeHtml(
          it.title
        )}</h4>
        <p class="text-xs text-fog">${fmtSec(it.position_sec)}${
          it.duration_sec ? ` / ${fmtSec(it.duration_sec)}` : ""
        }${it.percent != null ? ` · ${it.percent}%` : ""}</p>
        <div class="flex gap-2 mt-auto">
          <button type="button" class="btn-primary text-xs py-1.5 px-2.5" data-lib-watch="${escapeHtml(
            it.download_id
          )}">Resume</button>
          <button type="button" class="btn-ghost text-xs py-1.5 px-2.5" data-lib-clear="${escapeHtml(
            it.download_id
          )}">Remove</button>
        </div>
      </article>`
      )
      .join("");
    dls.innerHTML = (lib.items || [])
      .map(
        (d) => `
      <div class="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-ink-700/80 bg-ink-900/40 px-3 py-2">
        <div class="min-w-0">
          <p class="text-sm truncate">${escapeHtml(d.name)}</p>
          <p class="text-xs text-fog">${escapeHtml(d.status)} · ${Math.round(
          (d.progress || 0) * 100
        )}%</p>
        </div>
        <button type="button" class="btn-secondary text-xs" data-lib-watch="${escapeHtml(
          d.id
        )}">Watch</button>
      </div>`
      )
      .join("");
    animateIn("#library-continue article", { stagger: 30 });
  } catch (err) {
    cont.innerHTML = `<p class="text-signal text-sm">${escapeHtml(err.message)}</p>`;
  }
}

async function refreshHealth() {
  try {
    const h = await api.health();
    $("#health-phrase").textContent = h.phrase || "Ready";
    $("#health-status").textContent = h.healthy ? "Online" : "Degraded";
    const dot = $("#health-pill .pulse-dot");
    dot.className = `h-2 w-2 rounded-full pulse-dot ${
      h.healthy ? "bg-mint" : "bg-signal"
    }`;
    $("#meta-line").textContent = `v${h.version} · ${h.providers} providers · stream ${
      streamEngineReady ? "ready" : "…"
    }`;
    const badge = $("#dl-count");
    if (h.downloads_active > 0) {
      badge.textContent = String(h.downloads_active);
      badge.classList.add("show");
    } else {
      badge.textContent = "";
      badge.classList.remove("show");
    }
  } catch {
    $("#health-phrase").textContent = "Can’t reach API — start the server first";
    $("#health-status").textContent = "Offline";
  }
}

async function loadProviders() {
  try {
    providersCache = await api.providers();
    defaultProviderIds = new Set(
      providersCache.filter((p) => p.default).map((p) => p.id)
    );
    renderProviderList();
  } catch {
    providersCache = [];
  }
}

function renderProviderList() {
  const box = $("#provider-list");
  const nsfw = $("#nsfw").checked;
  const list = providersCache.filter((p) => nsfw || p.safety !== "unsafe");
  box.innerHTML = list
    .map(
      (p) => `<label class="check text-xs">
        <input type="checkbox" class="prov-cb" value="${escapeHtml(p.id)}"
          ${defaultProviderIds.has(p.id) ? "checked" : ""} />
        ${escapeHtml(p.name)}
      </label>`
    )
    .join("");
}

function selectedProviders() {
  const checked = $$(".prov-cb:checked").map((el) => el.value);
  return checked.length ? checked : null;
}

function torrentActions(t, idx) {
  const hasMagnet = !!t.magnet;
  const canResolve = !hasMagnet && !!t.magnet_fetch_url && t.db_id;
  const canStream = hasMagnet || !!t.db_id;
  const bits = [];
  if (canStream) {
    bits.push(
      `<button type="button" class="btn-tiny mint" data-act="stream" data-idx="${idx}">Stream</button>`
    );
    bits.push(
      `<button type="button" class="btn-tiny signal" data-act="download" data-idx="${idx}">Download</button>`
    );
  }
  if (canResolve) {
    bits.push(
      `<button type="button" class="btn-tiny" data-act="resolve" data-idx="${idx}">Get magnet</button>`
    );
  }
  if (hasMagnet) {
    bits.push(
      `<button type="button" class="btn-tiny" data-act="copy" data-idx="${idx}">Copy</button>`
    );
  }
  if (t.details_page_url) {
    bits.push(
      `<a class="btn-tiny" href="${escapeHtml(
        t.details_page_url
      )}" target="_blank" rel="noopener">Page</a>`
    );
  }
  if (!bits.length) bits.push(`<span class="badge warn">unavailable</span>`);
  return bits.join("");
}

function renderResults(payload) {
  lastResults = payload?.results || [];
  const box = $("#results");
  const empty = $("#results-empty");

  $("#search-meta").textContent = payload
    ? `${payload.count} results · ${payload.duration_ms} ms${
        payload.search_id ? ` · saved #${payload.search_id}` : ""
      }`
    : "";

  $("#search-errors").innerHTML = (payload?.errors || [])
    .map(
      (e) =>
        `<div class="text-signal">${escapeHtml(e.provider)} (${escapeHtml(
          e.kind
        )}): ${escapeHtml(e.message)}</div>`
    )
    .join("");

  if (!lastResults.length) {
    box.innerHTML = "";
    empty.classList.remove("hidden");
    empty.textContent =
      "No torrents matched. Try another query — or run the Sintel demo stream.";
    return;
  }
  empty.classList.add("hidden");

  box.innerHTML = lastResults
    .map((t, i) => {
      return `<article class="result-row" data-idx="${i}">
        <div class="flex flex-wrap gap-3 justify-between items-start">
          <div class="min-w-0 flex-1">
            <div class="flex flex-wrap gap-2 items-center mb-1">
              <span class="badge">#${t.rank ?? i + 1}</span>
              <span class="badge">${escapeHtml(t.provider)}</span>
              ${t.magnet ? `<span class="badge good">magnet</span>` : ""}
              ${t.dead ? `<span class="badge bad">dead</span>` : ""}
            </div>
            <h3 class="font-medium text-[0.95rem] leading-snug break-words">${escapeHtml(
              t.name
            )}</h3>
            <p class="text-xs text-fog mt-1.5">
              ${escapeHtml(t.size || "?")} · ${t.seeders ?? "—"} seeders · ${
        t.peers ?? "—"
      } peers
            </p>
          </div>
          <div class="flex flex-wrap gap-1.5 shrink-0">${torrentActions(t, i)}</div>
        </div>
      </article>`;
    })
    .join("");

  animateIn("#results .result-row");
}

async function ensureMagnet(row) {
  if (row.magnet) return row.magnet;
  if (!row.db_id) throw new Error("No magnet available");
  toast("Fetching magnet…");
  const out = await api.resolveMagnet(Number(row.db_id));
  if (!out.resolved || !out.magnet) {
    throw new Error("Could not resolve magnet for streaming");
  }
  row.magnet = out.magnet;
  return out.magnet;
}

async function startDownload(row, preset) {
  const body = { preset };
  if (row.db_id) body.torrent_result_id = Number(row.db_id);
  else if (row.magnet) body.magnet = row.magnet;
  else throw new Error("Nothing to download");
  const state = await api.createDownload(body);
  toast(`Started ${preset}`);
  switchTab("downloads");
  refreshDownloads();
  refreshHealth();
  return state;
}

function setPlayerOpen(open) {
  const el = $("#player-overlay");
  el.classList.toggle("hidden", !open);
  el.setAttribute("aria-hidden", open ? "false" : "true");
  if (!open) {
    const video = $("#player-video");
    video.pause();
    video.removeAttribute("src");
    while (video.firstChild) video.removeChild(video.firstChild);
    video.load();
    stopPlayer();
    $("#player-files").innerHTML = "";
    $("#player-title").textContent = "—";
    $("#player-status").textContent = "Starting…";
    const ct = $("#cinema-title");
    if (ct) ct.textContent = "—";
    $("#player-tracks")?.classList.add("hidden");
    $("#nerd-stats")?.classList.add("hidden");
  }
}

async function openStreamPlayer({ magnet, row, title, downloadId }) {
  let uri = magnet;
  if (!uri && row) uri = await ensureMagnet(row);
  if (!uri && !downloadId) throw new Error("No magnet to stream");

  setPlayerOpen(true);
  $("#player-title").textContent = title || row?.name || "Stream";
  const ct = $("#cinema-title");
  if (ct) ct.textContent = title || row?.name || "Stream";
  $("#player-status").textContent = downloadId
    ? "Opening stream…"
    : "Adding torrent on server…";
  $("#player-files").innerHTML = "";
  const video = $("#player-video");

  try {
    let id = downloadId;
    if (!id) {
      const state = await api.createDownload({
        magnet: uri,
        torrent_result_id: row?.db_id ? Number(row.db_id) : undefined,
        preset: "stream",
      });
      id = state.id;
      refreshHealth();
    }

    await playDownloadId({
      downloadId: id,
      video,
      status: (msg) => {
        $("#player-status").textContent = msg;
      },
      onReady: (info) => {
        if (info.name) $("#player-title").textContent = info.name;
      },
    });
    toast("Player ready");
  } catch (err) {
    $("#player-status").textContent = err.message;
    toast(err.message);
  }
}

async function onResultsClick(ev) {
  const btn = ev.target.closest("button[data-act]");
  if (!btn) return;
  const act = btn.dataset.act;
  const idx = Number(btn.dataset.idx);
  const row = lastResults[idx];
  if (!row && act !== "copy") return;

  try {
    btn.disabled = true;
    if (act === "copy") {
      const m = row?.magnet;
      if (!m) throw new Error("No magnet");
      await navigator.clipboard.writeText(m);
      toast("Magnet copied");
      return;
    }
    if (act === "resolve") {
      await ensureMagnet(row);
      renderResults({
        count: lastResults.length,
        duration_ms: "—",
        search_id: row.search_id,
        results: lastResults,
        errors: [],
      });
      toast("Magnet ready");
      return;
    }
    if (act === "stream") {
      await openStreamPlayer({ row, title: row.name });
      return;
    }
    if (act === "download") {
      await startDownload(row, "general");
    }
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
  }
}

async function onSearch(ev) {
  ev.preventDefault();
  const btn = $("#search-btn");
  btn.disabled = true;
  btn.textContent = "Searching…";
  $("#results").innerHTML = "";
  $("#results-empty").classList.add("hidden");
  $("#search-meta").textContent = "Searching providers…";

  try {
    const providers = selectedProviders();
    const body = {
      query: $("#q").value.trim(),
      category: $("#category").value,
      max_results: Number($("#max_results").value) || 40,
      hide_dead: $("#hide_dead").checked,
      resolve_magnets: $("#resolve_magnets").checked,
      all_providers: $("#all_providers").checked,
      nsfw: $("#nsfw").checked,
      save: true,
      sort: "seeders",
      order: "desc",
    };
    if (providers) body.providers = providers;
    const data = await api.search(body);
    renderResults(data);
    refreshHealth();
    toast(`Found ${data.count} torrents`);
  } catch (err) {
    $("#search-meta").textContent = "";
    $("#results-empty").classList.remove("hidden");
    $("#results-empty").textContent = err.message;
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "Search";
  }
}

async function loadPresets() {
  try {
    const presets = await api.presets();
    const sel = $("#dl-preset");
    sel.innerHTML = presets
      .map((p) => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.id)}</option>`)
      .join("");
    if ([...sel.options].some((o) => o.value === "stream")) sel.value = "stream";
  } catch {
    $("#dl-preset").innerHTML =
      `<option value="general">general</option><option value="stream">stream</option>`;
  }
}

function renderDownloads(items, { animate = true } = {}) {
  const box = $("#downloads");
  const empty = $("#downloads-empty");
  if (!items?.length) {
    box.innerHTML = "";
    empty.classList.remove("hidden");
    return;
  }
  empty.classList.add("hidden");
  box.innerHTML = items
    .map((d) => {
      const pct = fmtPct(d.progress);
      const canWatch =
        d.mode === "stream" && d.magnet_or_url?.startsWith("magnet:");
      return `<article class="dl-row" data-id="${escapeHtml(d.id)}">
        <div class="flex flex-wrap justify-between gap-2 items-start">
          <div class="min-w-0">
            <div class="flex flex-wrap gap-2 items-center mb-1">
              <span class="badge good">${escapeHtml(d.status)}</span>
              <span class="badge">${escapeHtml(d.mode)}</span>
              ${d.underrun ? `<span class="badge warn">buffering</span>` : ""}
            </div>
            <h3 class="font-medium break-words">${escapeHtml(d.name || d.id)}</h3>
            <p class="text-xs text-fog mt-1">
              ${fmtBytes(d.downloaded_bytes)} / ${fmtBytes(d.total_bytes)} ·
              peers ${d.num_peers}
            </p>
          </div>
          <div class="flex flex-wrap gap-1.5">
            ${
              canWatch
                ? `<button type="button" class="btn-tiny mint" data-act="watch" data-id="${escapeHtml(
                    d.id
                  )}">Watch</button>`
                : ""
            }
            <button type="button" class="btn-tiny" data-id="${escapeHtml(
              d.id
            )}" data-act="pause">Pause</button>
            <button type="button" class="btn-tiny mint" data-id="${escapeHtml(
              d.id
            )}" data-act="resume">Resume</button>
            <button type="button" class="btn-danger" data-id="${escapeHtml(
              d.id
            )}" data-act="remove">Remove</button>
          </div>
        </div>
        <div class="progress-track mt-3"><div class="progress-fill" style="width:${pct}"></div></div>
        <p class="text-[0.7rem] text-fog mt-1.5">${pct}</p>
      </article>`;
    })
    .join("");
  if (animate) animateIn("#downloads .dl-row", { stagger: 30 });
}

function fmtPct(p) {
  return `${Math.round((p || 0) * 100)}%`;
}

async function refreshDownloads({ animate = false } = {}) {
  try {
    renderDownloads(await api.downloads(), { animate });
  } catch (err) {
    if (activeTab === "downloads") toast(err.message);
  }
}

async function refreshHistory() {
  const box = $("#history");
  try {
    const page = await api.searches(50);
    if (!page.items?.length) {
      box.innerHTML = `<p class="text-fog text-center mt-12">No saved searches yet.</p>`;
      return;
    }
    box.innerHTML = page.items
      .map((s) => {
        const when = new Date(s.created_at).toLocaleString();
        return `<article class="hist-row flex flex-wrap justify-between gap-3 items-center">
          <button type="button" class="text-left min-w-0 flex-1 hist-open" data-id="${s.id}">
            <h3 class="font-medium">#${s.id} · ${escapeHtml(s.query)}</h3>
            <p class="text-xs text-fog mt-1">
              ${escapeHtml(s.category)} · ${s.result_count} results · ${escapeHtml(when)}
            </p>
          </button>
          <button type="button" class="btn-danger" data-del="${s.id}">Delete</button>
        </article>`;
      })
      .join("");
    animateIn("#history .hist-row");
  } catch (err) {
    box.innerHTML = `<p class="text-signal">${escapeHtml(err.message)}</p>`;
  }
}

async function openHistory(id) {
  const detail = await api.getSearch(id);
  renderResults({
    count: detail.results.length,
    duration_ms: detail.duration_ms ?? "—",
    search_id: detail.id,
    results: detail.results,
    errors: detail.errors || [],
  });
  $("#q").value = detail.query;
  switchTab("search");
  toast(`Opened “${detail.query}”`);
}

async function refreshActivity() {
  const list = $("#activity");
  try {
    const feed = await api.activity(60);
    if (!feed.events?.length) {
      list.innerHTML = `<li class="text-fog text-sm">Waiting for events…</li>`;
      return;
    }
    list.innerHTML = feed.events
      .slice()
      .reverse()
      .map((e) => {
        const t = new Date(e.time).toLocaleTimeString();
        return `<li class="activity-item ${escapeHtml(e.level)} py-2">
          <div class="flex gap-2 items-baseline">
            <span class="mark font-semibold">›</span>
            <span class="time">${escapeHtml(t)}</span>
            <span class="text-xs uppercase tracking-wider text-fog">${escapeHtml(
              e.area
            )}</span>
          </div>
          <p class="pl-4 text-sm mt-0.5">${escapeHtml(e.message)}</p>
        </li>`;
      })
      .join("");
    animateIn("#activity .activity-item", { x: true, stagger: 25 });
  } catch (err) {
    list.innerHTML = `<li class="text-signal text-sm">${escapeHtml(err.message)}</li>`;
  }
}

function wire() {
  $$(".tab-btn").forEach((btn) =>
    btn.addEventListener("click", () => switchTab(btn.dataset.tab))
  );
  $("#search-form").addEventListener("submit", onSearch);
  $("#results").addEventListener("click", onResultsClick);
  $("#toggle-providers").addEventListener("click", () => {
    $("#provider-panel").classList.toggle("hidden");
  });
  $("#nsfw").addEventListener("change", renderProviderList);
  $("#demo-stream").addEventListener("click", async () => {
    try {
      await openStreamPlayer({
        magnet: DEMO_MAGNET,
        title: "Sintel (demo)",
      });
    } catch (err) {
      toast(err.message);
    }
  });

  $("#prov-defaults").addEventListener("click", () => {
    $$(".prov-cb").forEach((cb) => {
      cb.checked = defaultProviderIds.has(cb.value);
    });
  });
  $("#prov-none").addEventListener("click", () => {
    $$(".prov-cb").forEach((cb) => {
      cb.checked = false;
    });
  });

  $("#library-refresh")?.addEventListener("click", () => refreshLibrary());
  $("#panel-library")?.addEventListener("click", async (ev) => {
    const watch = ev.target.closest("[data-lib-watch]");
    const clear = ev.target.closest("[data-lib-clear]");
    if (watch) {
      const id = watch.getAttribute("data-lib-watch");
      try {
        await openStreamPlayer({ downloadId: id, title: "Library" });
      } catch (err) {
        toast(err.message);
      }
      return;
    }
    if (clear) {
      const id = clear.getAttribute("data-lib-clear");
      try {
        await fetch(`/api/v1/library/continue/${encodeURIComponent(id)}`, {
          method: "DELETE",
        });
        refreshLibrary();
      } catch (err) {
        toast(err.message);
      }
    }
  });

  $("#dl-add").addEventListener("click", async () => {
    const magnet = $("#dl-magnet").value.trim();
    if (!magnet.startsWith("magnet:")) {
      toast("Paste a full magnet:? link");
      return;
    }
    const preset = $("#dl-preset").value || "general";
    try {
      if (preset === "stream") {
        await openStreamPlayer({ magnet, title: "Stream" });
      } else {
        await api.createDownload({ magnet, preset });
        toast("Download started");
        switchTab("downloads");
        refreshDownloads();
      }
      $("#dl-magnet").value = "";
    } catch (err) {
      toast(err.message);
    }
  });

  $("#downloads").addEventListener("click", async (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) return;
    const id = btn.dataset.id;
    const act = btn.dataset.act;
    try {
      if (act === "watch") {
        const items = await api.downloads();
        const d = items.find((x) => x.id === id);
        if (!d) throw new Error("Download not found");
        await openStreamPlayer({
          downloadId: id,
          magnet: d.magnet_or_url,
          title: d.name || "Stream",
        });
        return;
      }
      if (act === "pause") await api.pause(id);
      if (act === "resume") await api.resume(id);
      if (act === "remove") await api.removeDownload(id);
      await refreshDownloads({ animate: false });
    } catch (err) {
      toast(err.message);
    }
  });

  $("#history-refresh").addEventListener("click", refreshHistory);
  $("#history").addEventListener("click", async (ev) => {
    const open = ev.target.closest(".hist-open");
    const del = ev.target.closest("button[data-del]");
    try {
      if (open) await openHistory(open.dataset.id);
      if (del) {
        await api.deleteSearch(del.dataset.del);
        toast("Deleted");
        refreshHistory();
      }
    } catch (err) {
      toast(err.message);
    }
  });

  $("#activity-refresh").addEventListener("click", refreshActivity);
  $("#player-close").addEventListener("click", () => setPlayerOpen(false));
  $("#player-overlay").addEventListener("click", (ev) => {
    if (ev.target === $("#player-overlay")) setPlayerOpen(false);
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && !$("#player-overlay").classList.contains("hidden")) {
      setPlayerOpen(false);
    }
  });

  setInterval(refreshHealth, 4000);
  setInterval(() => {
    if (activeTab === "downloads") refreshDownloads({ animate: false });
  }, 1200);
  setInterval(() => {
    if (activeTab === "activity") refreshActivity();
  }, 3000);
}

async function boot() {
  wire();
  await Promise.all([loadProviders(), loadPresets(), refreshHealth()]);
  await refreshActivity();

  // Warm SW / WebTorrent so the first Stream click works.
  streamEngineReady = await warmStreamEngine((msg) => {
    $("#meta-line").textContent = msg;
  });
  refreshHealth();

  if (typeof anime !== "undefined") {
    anime
      .timeline({ easing: "easeOutCubic" })
      .add({
        targets: "header h1",
        opacity: [0, 1],
        translateY: [16, 0],
        duration: 650,
      })
      .add(
        {
          targets: "#health-phrase, #health-pill, .tab-btn",
          opacity: [0, 1],
          translateY: [8, 0],
          delay: anime.stagger(50),
          duration: 420,
        },
        "-=380"
      );
  }
}

boot();
