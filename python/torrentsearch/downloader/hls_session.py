"""ffmpeg HLS sessions — video/audio remux + independent WebVTT subtitle extract."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..activity import ActivityLevel, activity
from .ffmpeg_util import ffmpeg_path

# Codecs browsers can't show as <track> WebVTT
BITMAP_SUB_CODECS = {
    "hdmv_pgs_subtitle",
    "dvd_subtitle",
    "dvdsub",
    "pgssub",
    "xsub",
    "dvb_subtitle",
}


@dataclass
class HlsSession:
    download_id: str
    media_url: str
    audio_index: Optional[int]
    subtitle_index: Optional[int]
    work_dir: Path
    proc: Optional[asyncio.subprocess.Process] = None
    sub_proc: Optional[asyncio.subprocess.Process] = None
    playlist: Path = field(init=False)
    subtitle_vtt: Path = field(init=False)
    ready: asyncio.Event = field(default_factory=asyncio.Event)
    error: Optional[str] = None
    sub_error: Optional[str] = None
    _stderr_buf: bytes = field(default=b"", repr=False)

    def __post_init__(self) -> None:
        self.playlist = self.work_dir / "index.m3u8"
        self.subtitle_vtt = self.work_dir / "subs.vtt"


class HlsManager:
    def __init__(self) -> None:
        self._sessions: dict[str, HlsSession] = {}
        self._lock = asyncio.Lock()

    def get(self, download_id: str) -> Optional[HlsSession]:
        return self._sessions.get(download_id)

    async def stop(self, download_id: str) -> None:
        async with self._lock:
            sess = self._sessions.pop(download_id, None)
        if sess:
            await self._kill(sess)

    async def stop_all(self) -> None:
        async with self._lock:
            ids = list(self._sessions.keys())
        for did in ids:
            await self.stop(did)

    async def ensure(
        self,
        download_id: str,
        media_url: str,
        *,
        audio_index: Optional[int] = None,
        subtitle_index: Optional[int] = None,
    ) -> HlsSession:
        """
        Ensure video+audio HLS is running for this audio track.
        Subtitle changes do NOT remux video — use set_subtitle().
        """
        async with self._lock:
            cur = self._sessions.get(download_id)
            reuse = (
                cur
                and cur.media_url == media_url
                and cur.audio_index == audio_index
                and cur.proc
                and cur.proc.returncode is None
                and cur.playlist.is_file()
            )
            if reuse and cur:
                # Keep video; optionally refresh subs without restart
                if subtitle_index != cur.subtitle_index:
                    await self._extract_subtitle(cur, subtitle_index)
                return cur

            if cur:
                self._sessions.pop(download_id, None)
                await self._kill(cur)

            work = Path(tempfile.mkdtemp(prefix=f"ts-hls-{download_id}-"))
            sess = HlsSession(
                download_id=download_id,
                media_url=media_url,
                audio_index=audio_index,
                subtitle_index=None,
                work_dir=work,
            )
            self._sessions[download_id] = sess

        await activity.say(
            f"Starting ffmpeg HLS for {download_id}"
            + (f" (audio#{audio_index})" if audio_index is not None else ""),
            area="download",
        )
        ok = await self._run_ffmpeg(sess, transcode=False)
        if not ok:
            await activity.say(
                "Remux copy failed — transcoding with libx264…",
                area="download",
            )
            ok = await self._run_ffmpeg(sess, transcode=True)
        if not ok:
            raise RuntimeError(sess.error or "ffmpeg HLS failed")

        if subtitle_index is not None:
            await self._extract_subtitle(sess, subtitle_index)
        return sess

    async def set_subtitle(
        self,
        download_id: str,
        subtitle_index: Optional[int],
    ) -> HlsSession:
        """Swap softsubs without touching the video/audio ffmpeg process."""
        sess = self._sessions.get(download_id)
        if not sess:
            raise RuntimeError("No HLS session — start playback first")
        await self._extract_subtitle(sess, subtitle_index)
        return sess

    async def _kill(self, sess: HlsSession) -> None:
        for proc in (sess.proc, sess.sub_proc):
            if proc and proc.returncode is None:
                try:
                    proc.terminate()
                    try:
                        await asyncio.wait_for(proc.wait(), timeout=3)
                    except asyncio.TimeoutError:
                        proc.kill()
                        await proc.wait()
                except ProcessLookupError:
                    pass
        try:
            shutil.rmtree(sess.work_dir, ignore_errors=True)
        except Exception:
            pass

    def _audio_map(self, sess: HlsSession) -> str:
        if sess.audio_index is not None:
            return f"0:{sess.audio_index}"
        return "0:a:0?"

    async def _extract_subtitle(
        self, sess: HlsSession, subtitle_index: Optional[int]
    ) -> None:
        # Stop previous extract
        if sess.sub_proc and sess.sub_proc.returncode is None:
            try:
                sess.sub_proc.kill()
                await sess.sub_proc.wait()
            except ProcessLookupError:
                pass
        sess.sub_proc = None
        sess.sub_error = None
        sess.subtitle_index = subtitle_index

        if sess.subtitle_vtt.is_file():
            try:
                sess.subtitle_vtt.unlink()
            except OSError:
                pass

        if subtitle_index is None:
            return

        ff = ffmpeg_path()
        if not ff:
            sess.sub_error = "ffmpeg missing"
            raise RuntimeError(sess.sub_error)

        await activity.say(
            f"Extracting subtitle #{subtitle_index} → WebVTT…",
            area="download",
        )

        # Prefer webvtt encoder; fall back to raw webvtt muxer.
        attempts = [
            [
                str(ff),
                "-hide_banner",
                "-loglevel",
                "error",
                "-err_detect",
                "ignore_err",
                "-i",
                sess.media_url,
                "-map",
                f"0:{subtitle_index}",
                "-c:s",
                "webvtt",
                "-y",
                str(sess.subtitle_vtt),
            ],
            [
                str(ff),
                "-hide_banner",
                "-loglevel",
                "error",
                "-err_detect",
                "ignore_err",
                "-i",
                sess.media_url,
                "-map",
                f"0:{subtitle_index}",
                "-f",
                "webvtt",
                "-y",
                str(sess.subtitle_vtt),
            ],
        ]

        last_err = ""
        for cmd in attempts:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            sess.sub_proc = proc
            try:
                _, err = await asyncio.wait_for(proc.communicate(), timeout=90)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                last_err = "subtitle extract timed out"
                continue

            last_err = (err or b"").decode("utf-8", errors="replace")[-400:]
            if (
                proc.returncode == 0
                and sess.subtitle_vtt.is_file()
                and sess.subtitle_vtt.stat().st_size > 0
            ):
                # Ensure WEBVTT header exists
                text = sess.subtitle_vtt.read_text("utf-8", errors="replace")
                if not text.lstrip().upper().startswith("WEBVTT"):
                    sess.subtitle_vtt.write_text(
                        "WEBVTT\n\n" + text, encoding="utf-8"
                    )
                await activity.say(
                    "Subtitles ready",
                    level=ActivityLevel.GOOD,
                    area="download",
                )
                return

        sess.sub_error = (
            last_err.strip()
            or "Could not convert subtitle (bitmap PGS/VobSub needs burn-in)"
        )
        raise RuntimeError(sess.sub_error)

    async def _run_ffmpeg(self, sess: HlsSession, *, transcode: bool) -> bool:
        ff = ffmpeg_path()
        if not ff:
            sess.error = "ffmpeg not found under python/etc"
            return False

        if sess.proc and sess.proc.returncode is None:
            try:
                sess.proc.kill()
                await sess.proc.wait()
            except ProcessLookupError:
                pass

        for p in sess.work_dir.glob("seg_*"):
            try:
                p.unlink()
            except OSError:
                pass
        if sess.playlist.is_file():
            try:
                sess.playlist.unlink()
            except OSError:
                pass

        if transcode:
            vcodec = [
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-tune",
                "zerolatency",
                "-pix_fmt",
                "yuv420p",
            ]
        else:
            vcodec = ["-c:v", "copy"]

        # Explicit AAC encode so browser HLS always has a playable audio track
        # even when source is DTS/TrueHD/Atmos.
        cmd = [
            str(ff),
            "-hide_banner",
            "-loglevel",
            "warning",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            sess.media_url,
            "-map",
            "0:v:0",
            "-map",
            self._audio_map(sess),
            *vcodec,
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-b:a",
            "192k",
            "-f",
            "hls",
            "-hls_time",
            "4",
            "-hls_list_size",
            "0",
            "-hls_flags",
            "independent_segments+append_list+omit_endlist",
            "-hls_segment_filename",
            str(sess.work_dir / "seg_%05d.ts"),
            str(sess.playlist),
        ]

        sess.proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(sess.work_dir),
        )
        asyncio.create_task(self._drain_stderr(sess, sess.proc))

        deadline = 90 if transcode else 40
        steps = int(deadline / 0.25)
        for _ in range(steps):
            if sess.playlist.is_file() and sess.playlist.stat().st_size > 0:
                sess.ready.set()
                await activity.say(
                    "HLS playlist ready"
                    + (" (transcode)" if transcode else ""),
                    level=ActivityLevel.GOOD,
                    area="download",
                )
                return True
            if sess.proc.returncode is not None:
                detail = sess._stderr_buf.decode("utf-8", errors="replace")[-600:]
                sess.error = detail or f"ffmpeg exited ({sess.proc.returncode})"
                return False
            await asyncio.sleep(0.25)

        if sess.proc.returncode is None and sess.playlist.is_file():
            sess.ready.set()
            return True
        sess.error = sess.error or "Timed out waiting for HLS playlist"
        return False

    async def _drain_stderr(
        self, sess: HlsSession, proc: asyncio.subprocess.Process
    ) -> None:
        if not proc.stderr:
            return
        try:
            while True:
                chunk = await proc.stderr.read(4096)
                if not chunk:
                    break
                sess._stderr_buf = (sess._stderr_buf + chunk)[-8000:]
        except Exception:
            pass
        await proc.wait()
        if proc.returncode not in (0, None) and sess._stderr_buf:
            text = sess._stderr_buf.decode("utf-8", errors="replace")[-200:]
            await activity.say(
                f"ffmpeg HLS ended: {text}",
                level=ActivityLevel.BAD,
                area="download",
            )


hls_manager = HlsManager()
