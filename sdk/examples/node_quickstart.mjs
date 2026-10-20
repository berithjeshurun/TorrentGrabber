/**
 * Start torrent-engine separately, then:
 *   node sdk/examples/node_quickstart.mjs 'magnet:?xt=…'
 */
import { TorrentEngine, EngineError } from "../javascript/index.js";

const magnet = process.argv[2];
if (!magnet) {
  console.error("usage: node_quickstart.mjs 'magnet:?xt=…'");
  process.exit(2);
}

const base = process.env.TORRENT_ENGINE_URL || "http://127.0.0.1:17890";
const eng = new TorrentEngine(base);

const health = await eng.health();
console.log("health", health.ok);

const job = await eng.add(magnet, { mode: "stream" });
console.log("id", job.id);
console.log("media", eng.mediaUrl(job.id));

for (let i = 0; i < 40; i++) {
  const buf = await eng.buffer(job.id);
  console.log("ahead", buf.contiguous_ahead_bytes, buf.warning || "");
  try {
    const hls = await eng.startHls(job.id);
    console.log("playlist", hls.playlist_url || eng.hlsPlaylistUrl(job.id));
    process.exit(0);
  } catch (e) {
    if (e instanceof EngineError && e.status === 503 && e.data?.retry) {
      await new Promise((r) => setTimeout(r, 2000));
      continue;
    }
    throw e;
  }
}
console.error("timed out waiting for HLS gate");
process.exit(1);
