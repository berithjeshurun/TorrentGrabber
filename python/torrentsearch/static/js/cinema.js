/**
 * Cinema — Netflix-style custom HTML5 video chrome.
 * No third-party player dependency; wraps a <video> already in the DOM.
 */

function fmtTime(sec) {
  if (!Number.isFinite(sec) || sec < 0) return "0:00";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  const s = Math.floor(sec % 60);
  const mm = h > 0 ? String(m).padStart(2, "0") : String(m);
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

export class CinemaPlayer {
  /**
   * @param {object} opts
   * @param {HTMLElement} opts.root  .cinema root
   * @param {HTMLVideoElement} opts.video
   * @param {() => void} [opts.onToggleStats]
   * @param {() => void} [opts.onToggleTracks]
   */
  constructor({
    root,
    video,
    onToggleStats,
    onToggleTracks,
    onBoost,
    onCast,
    onInvite,
    onAbr,
  }) {
    this.root = root;
    this.video = video;
    this.onToggleStats = onToggleStats || (() => {});
    this.onToggleTracks = onToggleTracks || (() => {});
    this.onBoost = onBoost || (() => {});
    this.onCast = onCast || (() => {});
    this.onInvite = onInvite || (() => {});
    this.onAbr = onAbr || (() => {});
    this._hideTimer = null;
    this._raf = null;
    this._bound = false;

    this.els = {
      play: root.querySelector("[data-cinema=play]"),
      back: root.querySelector("[data-cinema=back]"),
      fwd: root.querySelector("[data-cinema=fwd]"),
      mute: root.querySelector("[data-cinema=mute]"),
      vol: root.querySelector("[data-cinema=vol]"),
      fs: root.querySelector("[data-cinema=fs]"),
      stats: root.querySelector("[data-cinema=stats]"),
      tracks: root.querySelector("[data-cinema=tracks]"),
      boost: root.querySelector("[data-cinema=boost]"),
      cast: root.querySelector("[data-cinema=cast]"),
      invite: root.querySelector("[data-cinema=invite]"),
      abr: root.querySelector("[data-cinema=abr]"),
      wave: root.querySelector("[data-cinema=wave]"),
      scrub: root.querySelector("[data-cinema=scrub]"),
      buf: root.querySelector("[data-cinema=buf]"),
      played: root.querySelector("[data-cinema=played]"),
      thumb: root.querySelector("[data-cinema=thumb]"),
      cur: root.querySelector("[data-cinema=cur]"),
      dur: root.querySelector("[data-cinema=dur]"),
      big: root.querySelector("[data-cinema=bigplay]"),
      live: root.querySelector("[data-cinema=live]"),
    };

    video.controls = false;
    video.playsInline = true;
    this._wire();
    this._tick();
  }

  _wire() {
    if (this._bound) return;
    this._bound = true;
    const v = this.video;
    const {
      play,
      back,
      fwd,
      mute,
      vol,
      fs,
      stats,
      tracks,
      scrub,
      big,
      boost,
      cast,
      invite,
      abr,
    } = this.els;

    const show = () => this.showChrome();
    this.root.addEventListener("mousemove", show);
    this.root.addEventListener("pointerdown", show);
    this.root.addEventListener("keydown", (e) => this._onKey(e));
    this.root.tabIndex = 0;

    play?.addEventListener("click", () => this.togglePlay());
    big?.addEventListener("click", () => this.togglePlay());
    v.addEventListener("click", () => this.togglePlay());
    back?.addEventListener("click", () => {
      v.currentTime = Math.max(0, v.currentTime - 10);
    });
    fwd?.addEventListener("click", () => {
      v.currentTime = Math.min(v.duration || 0, v.currentTime + 10);
    });
    mute?.addEventListener("click", () => {
      v.muted = !v.muted;
      this._syncVolUi();
    });
    vol?.addEventListener("input", () => {
      v.volume = Number(vol.value);
      v.muted = v.volume === 0;
      this._syncVolUi();
    });
    fs?.addEventListener("click", () => this.toggleFullscreen());
    stats?.addEventListener("click", () => this.onToggleStats());
    tracks?.addEventListener("click", () => this.onToggleTracks());
    boost?.addEventListener("click", () => this.onBoost());
    cast?.addEventListener("click", () => this.onCast());
    invite?.addEventListener("click", () => this.onInvite());
    abr?.addEventListener("change", () => this.onAbr(Number(abr.value) || 0));

    scrub?.addEventListener("pointerdown", (e) => this._scrubStart(e));
    scrub?.addEventListener("click", (e) => this._scrubTo(e));

    v.addEventListener("play", () => this._syncPlayUi());
    v.addEventListener("pause", () => this._syncPlayUi());
    v.addEventListener("volumechange", () => this._syncVolUi());
    v.addEventListener("waiting", () => this.root.classList.add("is-buffering"));
    v.addEventListener("playing", () =>
      this.root.classList.remove("is-buffering")
    );
    v.addEventListener("loadedmetadata", () => this._tick());

    this._syncPlayUi();
    this._syncVolUi();
    this.showChrome();
  }

  _onKey(e) {
    const v = this.video;
    switch (e.key) {
      case " ":
      case "k":
        e.preventDefault();
        this.togglePlay();
        break;
      case "ArrowLeft":
      case "j":
        e.preventDefault();
        v.currentTime = Math.max(0, v.currentTime - 10);
        break;
      case "ArrowRight":
      case "l":
        e.preventDefault();
        v.currentTime = Math.min(v.duration || 0, v.currentTime + 10);
        break;
      case "ArrowUp":
        e.preventDefault();
        v.volume = Math.min(1, v.volume + 0.05);
        v.muted = false;
        break;
      case "ArrowDown":
        e.preventDefault();
        v.volume = Math.max(0, v.volume - 0.05);
        break;
      case "m":
        v.muted = !v.muted;
        break;
      case "f":
        this.toggleFullscreen();
        break;
      case "i":
        this.onToggleStats();
        break;
      default:
        break;
    }
    this.showChrome();
  }

  togglePlay() {
    const v = this.video;
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  }

  async toggleFullscreen() {
    const el = this.root;
    try {
      if (!document.fullscreenElement) await el.requestFullscreen();
      else await document.exitFullscreen();
    } catch {
      /* ignore */
    }
  }

  showChrome() {
    this.root.classList.add("chrome-visible");
    this.root.classList.remove("chrome-hidden");
    clearTimeout(this._hideTimer);
    if (!this.video.paused) {
      this._hideTimer = setTimeout(() => {
        this.root.classList.remove("chrome-visible");
        this.root.classList.add("chrome-hidden");
      }, 2800);
    }
  }

  _syncPlayUi() {
    const paused = this.video.paused;
    this.root.classList.toggle("is-paused", paused);
    this.root.classList.toggle("is-playing", !paused);
    if (this.els.play) {
      this.els.play.setAttribute("aria-label", paused ? "Play" : "Pause");
      this.els.play.innerHTML = paused ? ICON_PLAY : ICON_PAUSE;
    }
    if (paused) this.showChrome();
  }

  _syncVolUi() {
    const v = this.video;
    if (this.els.vol) this.els.vol.value = String(v.muted ? 0 : v.volume);
    if (this.els.mute) {
      this.els.mute.innerHTML =
        v.muted || v.volume === 0 ? ICON_MUTE : ICON_VOL;
    }
  }

  _scrubRatio(e) {
    const bar = this.els.scrub;
    if (!bar) return 0;
    const r = bar.getBoundingClientRect();
    return Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
  }

  _scrubTo(e) {
    const v = this.video;
    if (!Number.isFinite(v.duration) || v.duration <= 0) return;
    v.currentTime = this._scrubRatio(e) * v.duration;
  }

  _scrubStart(e) {
    e.preventDefault();
    const move = (ev) => this._scrubTo(ev);
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    this._scrubTo(e);
  }

  _tick() {
    const v = this.video;
    const dur = v.duration || 0;
    const cur = v.currentTime || 0;
    const pct = dur > 0 ? (cur / dur) * 100 : 0;

    if (this.els.played) this.els.played.style.width = `${pct}%`;
    if (this.els.thumb) this.els.thumb.style.left = `${pct}%`;
    if (this.els.cur) this.els.cur.textContent = fmtTime(cur);
    if (this.els.dur) this.els.dur.textContent = fmtTime(dur);

    // Buffered ranges (union end as %)
    let bufEnd = 0;
    try {
      for (let i = 0; i < v.buffered.length; i++) {
        bufEnd = Math.max(bufEnd, v.buffered.end(i));
      }
    } catch {
      /* ignore */
    }
    const bufPct = dur > 0 ? (bufEnd / dur) * 100 : 0;
    if (this.els.buf) this.els.buf.style.width = `${bufPct}%`;

    this._raf = requestAnimationFrame(() => this._tick());
  }

  setLiveHint(on, text) {
    this.els.live?.classList.toggle("hidden", !on);
    if (text && this.els.live) this.els.live.textContent = text;
  }

  setAbrVisible(on) {
    this.els.abr?.classList.toggle("hidden", !on);
  }

  drawWaveform(peaks) {
    const canvas = this.els.wave;
    if (!canvas || !peaks?.length) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "rgba(255,255,255,0.35)";
    const mid = h / 2;
    const step = Math.max(1, Math.floor(peaks.length / w));
    for (let x = 0; x < w; x++) {
      const i = Math.min(peaks.length - 1, x * step);
      const v = Math.abs(Number(peaks[i]) || 0);
      const amp = Math.min(mid, v * mid);
      ctx.fillRect(x, mid - amp, 1, amp * 2 || 1);
    }
  }

  setChapters(chapters) {
    this._chapters = Array.isArray(chapters) ? chapters : [];
    const scrub = this.els.scrub;
    if (!scrub) return;
    scrub.querySelectorAll(".cinema-chapter").forEach((n) => n.remove());
    const dur = this.video?.duration;
    if (!Number.isFinite(dur) || dur <= 0 || !this._chapters.length) {
      if (this._chapters.length && this.video) {
        this.video.addEventListener(
          "loadedmetadata",
          () => this.setChapters(this._chapters),
          { once: true }
        );
      }
      return;
    }
    for (const c of this._chapters) {
      const t = Number(c.start ?? c.time ?? c.t ?? c);
      if (!Number.isFinite(t) || t < 0) continue;
      const mark = document.createElement("span");
      mark.className = "cinema-chapter";
      mark.style.left = `${Math.min(100, (t / dur) * 100)}%`;
      mark.title = c.title || c.name || fmtTime(t);
      scrub.appendChild(mark);
    }
  }

  destroy() {
    clearTimeout(this._hideTimer);
    if (this._raf) cancelAnimationFrame(this._raf);
  }
}

const ICON_PLAY =
  '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M8 5v14l11-7z"/></svg>';
const ICON_PAUSE =
  '<svg viewBox="0 0 24 24" width="22" height="22" fill="currentColor"><path d="M6 5h4v14H6zm8 0h4v14h-4z"/></svg>';
const ICON_VOL =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M3 10v4h4l5 5V5L7 10H3zm13.5 2a4.5 4.5 0 0 0-2.5-4v8a4.5 4.5 0 0 0 2.5-4z"/></svg>';
const ICON_MUTE =
  '<svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor"><path d="M16.5 12a4.5 4.5 0 0 0-2.5-4v2.2l2.5 2.5V12zm2.5 0c0 .9-.2 1.7-.5 2.5l1.5 1.5c.6-1.2 1-2.5 1-4 0-3.5-2.4-6.4-5.5-7.3v2.1c2 .8 3.5 2.8 3.5 5.2zM4.3 3 3 4.3 7.7 9H3v4h4l5 5v-6.7l5.7 5.7 1.3-1.3L4.3 3zM12 5l-1.1 1.1L12 7.2V5z"/></svg>';
