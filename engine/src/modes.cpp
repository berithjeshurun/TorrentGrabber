#include "modes.hpp"

namespace te {

DownloadMode parse_mode(const std::string& s) {
  if (s == "stream" || s == "stream_smooth" || s == "stream_catchup")
    return DownloadMode::Stream;
  if (s == "selective") return DownloadMode::Selective;
  if (s == "preview") return DownloadMode::Preview;
  if (s == "seed") return DownloadMode::Seed;
  if (s == "limited") return DownloadMode::Limited;
  if (s == "fastest") return DownloadMode::Fastest;
  if (s == "mirror") return DownloadMode::Mirror;
  if (s == "friend" || s == "private" || s == "p2p")
    return DownloadMode::Friend;
  return DownloadMode::General;
}

const char* mode_name(DownloadMode m) {
  switch (m) {
    case DownloadMode::Stream:
      return "stream";
    case DownloadMode::Selective:
      return "selective";
    case DownloadMode::Preview:
      return "preview";
    case DownloadMode::Seed:
      return "seed";
    case DownloadMode::Limited:
      return "limited";
    case DownloadMode::Fastest:
      return "fastest";
    case DownloadMode::Mirror:
      return "mirror";
    case DownloadMode::Friend:
      return "friend";
    default:
      return "general";
  }
}

ModeProfile profile_for(DownloadMode mode) {
  ModeProfile p;
  p.mode = mode;
  switch (mode) {
    case DownloadMode::Stream:
      p.connections_limit = 400;
      p.active_downloads = 16;
      p.sequential = true;
      p.playhead_window_bytes = 32LL * 1024 * 1024;
      p.lookahead_bytes = 96LL * 1024 * 1024;
      break;
    case DownloadMode::Limited:
      p.connections_limit = 40;
      p.active_downloads = 3;
      p.download_rate_limit = 512 * 1024;
      p.upload_rate_limit = 64 * 1024;
      break;
    case DownloadMode::Fastest:
      p.connections_limit = 800;
      p.active_downloads = 32;
      p.sequential = true;
      p.playhead_window_bytes = 8LL * 1024 * 1024;
      break;
    case DownloadMode::Preview:
      p.connections_limit = 80;
      p.active_downloads = 4;
      p.sequential = true;
      p.preview_head_bytes = 16LL * 1024 * 1024;
      p.preview_tail_bytes = 2LL * 1024 * 1024;
      break;
    case DownloadMode::Seed:
      p.connections_limit = 200;
      p.upload_rate_limit = 0;
      break;
    case DownloadMode::Mirror:
      p.connections_limit = 500;
      p.active_downloads = 20;
      p.sequential = true;
      p.playhead_window_bytes = 16LL * 1024 * 1024;
      break;
    case DownloadMode::Selective:
      p.connections_limit = 200;
      break;
    case DownloadMode::Friend:
      // Quiet private swarm — no DHT/LSD/PEX; friends connect via add-peer / x.pe
      p.connections_limit = 20;
      p.active_downloads = 4;
      p.private_swarm = true;
      p.sequential = true;
      break;
    default:
      p.connections_limit = 200;
      p.active_downloads = 8;
      break;
  }
  return p;
}

}  // namespace te
