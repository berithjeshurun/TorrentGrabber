#pragma once

#include "modes.hpp"

#include <filesystem>
#include <optional>
#include <string>
#include <unordered_map>

namespace te {

struct SessionConfig {
  int connections_limit = 500;
  int active_downloads = 16;
  int active_seeds = 16;
  int active_limit = 32;
};

struct ShareConfig {
  bool private_by_default = true;
  // Host/IP friends should dial (Tailscale / public IP / LAN). Empty = no x.pe in magnet
  // unless auto_detect_public_host is true.
  std::string public_host;
  int listen_port = 0;  // 0 = libtorrent ephemeral; set for stable friend dialing
  // When public_host is empty, probe api.ipify.org once at startup for WAN dialing.
  bool auto_detect_public_host = false;
  // "manual" | "tailscale" — tailscale profile: stable listen_port + public_host only
  // (no WAN auto-detect; HTTP stays localhost).
  std::string profile = "manual";
};

// Live Cinema / HLS pipeline (ffmpeg). Filters force video transcode.
struct HlsConfig {
  std::string audio_bitrate = "256k";
  std::string audio_channels = "keep";  // stereo | keep
  int hls_time = 4;
  bool low_latency = true;
  bool loudnorm = false;
  std::string deinterlace = "auto";  // auto | on | off
  std::string tonemap = "auto";  // auto | off | hable | mobius | reinhard
  std::string lut_cube;          // optional .cube path (after tonemap)
  bool abr = false;              // 360/720/1080 master playlist
  bool burn_subs = false;        // default burn-in off; API can override
};

struct UpdateConfig {
  // HTTPS URL to update/manifest.json (or GitHub raw / Releases latest).
  // Override with env TORRENT_ENGINE_UPDATE_URL.
  std::string manifest_url;
  bool check_on_start = false;  // log if newer version exists
};

struct AppConfig {
  int port = 0;
  std::string bind = "127.0.0.1";
  std::filesystem::path data_dir = "engine-data";
  std::optional<std::filesystem::path> ffmpeg_dir;
  SessionConfig session;
  ShareConfig share;
  HlsConfig hls;
  UpdateConfig update;
  // Overrides layered on top of built-in profile_for() defaults.
  std::unordered_map<std::string, ModeProfile> mode_overrides;
  std::filesystem::path loaded_from;  // empty if none
};

// Resolve config path: explicit → %CD%\config.yaml → <exe>\config.yaml
std::optional<std::filesystem::path> find_config_file(
    const std::optional<std::filesystem::path>& explicit_path);

// Load YAML. Missing file → defaults. Throws std::runtime_error on parse errors.
AppConfig load_config(const std::optional<std::filesystem::path>& explicit_path);

// Built-in defaults with YAML mode overrides applied.
ModeProfile profile_for(DownloadMode mode, const AppConfig& cfg);

}  // namespace te
