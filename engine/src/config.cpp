#include "config.hpp"

#include <fstream>
#include <iostream>
#include <stdexcept>

#include <yaml-cpp/yaml.h>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

namespace te {
namespace {

std::int64_t mb_to_bytes(double mb) {
  return static_cast<std::int64_t>(mb * 1024.0 * 1024.0);
}

void apply_mode_node(ModeProfile& p, const YAML::Node& n) {
  if (!n || !n.IsMap()) return;
  if (n["connections_limit"]) p.connections_limit = n["connections_limit"].as<int>();
  if (n["active_downloads"]) p.active_downloads = n["active_downloads"].as<int>();
  if (n["download_rate_limit"])
    p.download_rate_limit = n["download_rate_limit"].as<int>();
  if (n["upload_rate_limit"]) p.upload_rate_limit = n["upload_rate_limit"].as<int>();
  if (n["download_rate_kbps"])
    p.download_rate_limit = n["download_rate_kbps"].as<int>() * 1024;
  if (n["upload_rate_kbps"])
    p.upload_rate_limit = n["upload_rate_kbps"].as<int>() * 1024;
  if (n["sequential"]) p.sequential = n["sequential"].as<bool>();
  if (n["private_swarm"]) p.private_swarm = n["private_swarm"].as<bool>();
  if (n["playhead_window_bytes"])
    p.playhead_window_bytes = n["playhead_window_bytes"].as<std::int64_t>();
  if (n["lookahead_bytes"])
    p.lookahead_bytes = n["lookahead_bytes"].as<std::int64_t>();
  if (n["playhead_window_mb"])
    p.playhead_window_bytes = mb_to_bytes(n["playhead_window_mb"].as<double>());
  if (n["lookahead_mb"])
    p.lookahead_bytes = mb_to_bytes(n["lookahead_mb"].as<double>());
  if (n["preview_head_bytes"])
    p.preview_head_bytes = n["preview_head_bytes"].as<std::int64_t>();
  if (n["preview_tail_bytes"])
    p.preview_tail_bytes = n["preview_tail_bytes"].as<std::int64_t>();
  if (n["preview_head_mb"])
    p.preview_head_bytes = mb_to_bytes(n["preview_head_mb"].as<double>());
  if (n["preview_tail_mb"])
    p.preview_tail_bytes = mb_to_bytes(n["preview_tail_mb"].as<double>());
}

std::optional<std::filesystem::path> exe_dir() {
#ifdef _WIN32
  char buf[MAX_PATH];
  if (GetModuleFileNameA(nullptr, buf, MAX_PATH)) {
    return std::filesystem::path(buf).parent_path();
  }
#endif
  return std::nullopt;
}

}  // namespace

std::optional<std::filesystem::path> find_config_file(
    const std::optional<std::filesystem::path>& explicit_path) {
  if (explicit_path) {
    if (std::filesystem::is_regular_file(*explicit_path)) return *explicit_path;
    throw std::runtime_error("config file not found: " + explicit_path->string());
  }
  // %CD%\config.yaml
  auto cwd = std::filesystem::current_path() / "config.yaml";
  if (std::filesystem::is_regular_file(cwd)) return cwd;
  if (auto dir = exe_dir()) {
    auto beside = *dir / "config.yaml";
    if (std::filesystem::is_regular_file(beside)) return beside;
  }
  return std::nullopt;
}

AppConfig load_config(const std::optional<std::filesystem::path>& explicit_path) {
  AppConfig cfg;
  auto path = find_config_file(explicit_path);
  if (!path) return cfg;

  YAML::Node root;
  try {
    root = YAML::LoadFile(path->string());
  } catch (const std::exception& ex) {
    throw std::runtime_error(std::string("failed to parse config.yaml: ") + ex.what());
  }
  if (!root || !root.IsMap()) {
    throw std::runtime_error("config.yaml root must be a mapping");
  }

  cfg.loaded_from = *path;
  if (root["port"]) cfg.port = root["port"].as<int>();
  if (root["bind"]) cfg.bind = root["bind"].as<std::string>();
  if (root["data_dir"]) cfg.data_dir = root["data_dir"].as<std::string>();
  if (root["ffmpeg_dir"]) cfg.ffmpeg_dir = root["ffmpeg_dir"].as<std::string>();

  if (root["session"] && root["session"].IsMap()) {
    auto s = root["session"];
    if (s["connections_limit"])
      cfg.session.connections_limit = s["connections_limit"].as<int>();
    if (s["active_downloads"])
      cfg.session.active_downloads = s["active_downloads"].as<int>();
    if (s["active_seeds"]) cfg.session.active_seeds = s["active_seeds"].as<int>();
    if (s["active_limit"]) cfg.session.active_limit = s["active_limit"].as<int>();
  }

  if (root["share"] && root["share"].IsMap()) {
    auto s = root["share"];
    if (s["private_by_default"])
      cfg.share.private_by_default = s["private_by_default"].as<bool>();
    if (s["public_host"]) cfg.share.public_host = s["public_host"].as<std::string>();
    if (s["listen_port"]) cfg.share.listen_port = s["listen_port"].as<int>();
    if (s["auto_detect_public_host"])
      cfg.share.auto_detect_public_host = s["auto_detect_public_host"].as<bool>();
    if (s["profile"]) cfg.share.profile = s["profile"].as<std::string>();
  }

  // Tailscale-first: prefer explicit public_host, no WAN ipify, keep a stable BT port.
  if (cfg.share.profile == "tailscale") {
    cfg.share.auto_detect_public_host = false;
    if (cfg.share.listen_port <= 0) cfg.share.listen_port = 6881;
  }

  if (root["hls"] && root["hls"].IsMap()) {
    auto h = root["hls"];
    if (h["audio_bitrate"]) cfg.hls.audio_bitrate = h["audio_bitrate"].as<std::string>();
    if (h["audio_channels"])
      cfg.hls.audio_channels = h["audio_channels"].as<std::string>();
    if (h["hls_time"]) cfg.hls.hls_time = h["hls_time"].as<int>();
    if (h["low_latency"]) cfg.hls.low_latency = h["low_latency"].as<bool>();
    if (h["loudnorm"]) cfg.hls.loudnorm = h["loudnorm"].as<bool>();
    if (h["deinterlace"]) cfg.hls.deinterlace = h["deinterlace"].as<std::string>();
    if (h["tonemap"]) cfg.hls.tonemap = h["tonemap"].as<std::string>();
    if (h["lut_cube"]) cfg.hls.lut_cube = h["lut_cube"].as<std::string>();
    if (h["abr"]) cfg.hls.abr = h["abr"].as<bool>();
    if (h["burn_subs"]) cfg.hls.burn_subs = h["burn_subs"].as<bool>();
  }

  if (root["update"] && root["update"].IsMap()) {
    auto u = root["update"];
    if (u["manifest_url"])
      cfg.update.manifest_url = u["manifest_url"].as<std::string>();
    if (u["check_on_start"])
      cfg.update.check_on_start = u["check_on_start"].as<bool>();
  }

  if (root["modes"] && root["modes"].IsMap()) {
    for (auto it = root["modes"].begin(); it != root["modes"].end(); ++it) {
      auto name = it->first.as<std::string>();
      auto mode = parse_mode(name);
      ModeProfile base = ::te::profile_for(mode);  // built-in defaults
      apply_mode_node(base, it->second);
      cfg.mode_overrides[mode_name(mode)] = base;
      // Also store under the yaml key if aliases were used
      cfg.mode_overrides[name] = base;
    }
  }

  return cfg;
}

ModeProfile profile_for(DownloadMode mode, const AppConfig& cfg) {
  auto it = cfg.mode_overrides.find(mode_name(mode));
  if (it != cfg.mode_overrides.end()) return it->second;
  return ::te::profile_for(mode);
}

}  // namespace te
