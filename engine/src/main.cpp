#include "config.hpp"
#include "ffmpeg_bridge.hpp"
#include "http_server.hpp"
#include "party_manager.hpp"
#include "torrent_manager.hpp"
#include "updater.hpp"
#include "util.hpp"
#include "version.hpp"

#include <atomic>
#include <chrono>
#include <cstdio>
#include <csignal>
#include <filesystem>
#include <iostream>
#include <memory>
#include <optional>
#include <string>
#include <thread>

namespace {
std::atomic<te::HttpServer*> g_server{nullptr};

void on_signal(int) {
  if (auto* s = g_server.load()) s->stop();
}
}  // namespace

static void usage() {
  std::cerr
      << "torrent-engine — overkill libtorrent streaming sidecar\n"
      << "Usage: torrent-engine [--port N] [--bind ADDR] [--data-dir PATH]\n"
      << "                     [--ffmpeg-dir PATH] [--config PATH]\n"
      << "                     [--version] [--check-update] [--update]\n"
      << "  --port 0          listen on ephemeral port (default)\n"
      << "  --bind 127.0.0.1  bind address (default localhost)\n"
      << "  --data-dir DIR    session / download root (default ./engine-data)\n"
      << "  --ffmpeg-dir DIR  directory containing ffmpeg + ffprobe\n"
      << "  --config PATH     YAML config (default: %CD%\\config.yaml, then "
         "<exe>\\config.yaml)\n"
      << "  --version         print engine version JSON and exit\n"
      << "  --check-update    fetch update manifest (no install)\n"
      << "  --update          download + apply engine update if newer\n"
      << "  Update URL: config update.manifest_url or TORRENT_ENGINE_UPDATE_URL\n";
}

int main(int argc, char** argv) {
#ifdef _WIN32
  setvbuf(stdout, nullptr, _IONBF, 0);
  setvbuf(stderr, nullptr, _IONBF, 0);
#endif

  std::optional<std::filesystem::path> config_path;
  std::optional<int> port_override;
  std::optional<std::string> bind_override;
  std::optional<std::filesystem::path> data_override;
  std::optional<std::filesystem::path> ffmpeg_override;
  bool flag_version = false;
  bool flag_check_update = false;
  bool flag_update = false;

  for (int i = 1; i < argc; ++i) {
    std::string a = argv[i];
    auto need = [&](const char* name) -> std::string {
      if (i + 1 >= argc) {
        std::cerr << "missing value for " << name << "\n";
        std::exit(2);
      }
      return argv[++i];
    };
    if (a == "--help" || a == "-h") {
      usage();
      return 0;
    } else if (a == "--version" || a == "-V") {
      flag_version = true;
    } else if (a == "--check-update") {
      flag_check_update = true;
    } else if (a == "--update") {
      flag_update = true;
    } else if (a == "--port") {
      port_override = std::stoi(need("--port"));
    } else if (a == "--bind") {
      bind_override = need("--bind");
    } else if (a == "--data-dir") {
      data_override = need("--data-dir");
    } else if (a == "--ffmpeg-dir") {
      ffmpeg_override = need("--ffmpeg-dir");
    } else if (a == "--config") {
      config_path = need("--config");
    } else {
      std::cerr << "unknown arg: " << a << "\n";
      usage();
      return 2;
    }
  }

  if (flag_version) {
    std::cout << te::version_json().dump(2) << "\n";
    return 0;
  }

  te::AppConfig app;
  try {
    app = te::load_config(config_path);
  } catch (const std::exception& ex) {
    std::cerr << "[engine] " << ex.what() << "\n";
    return 2;
  }

  if (flag_check_update || flag_update) {
    auto url = te::resolve_manifest_url(app.update.manifest_url);
    return te::run_update_cli(flag_update, url);
  }
  if (!app.loaded_from.empty()) {
    std::cerr << "[engine] config: " << app.loaded_from.string() << "\n";
  } else {
    std::cerr << "[engine] config: defaults (no config.yaml found)\n";
  }

  // CLI overrides win over YAML
  if (port_override) app.port = *port_override;
  if (bind_override) app.bind = *bind_override;
  if (data_override) app.data_dir = *data_override;
  if (ffmpeg_override) app.ffmpeg_dir = *ffmpeg_override;

  std::filesystem::create_directories(app.data_dir);
  std::filesystem::create_directories(app.data_dir / "hls");

  auto ffmpeg_dir = te::find_ffmpeg_dir(app.ffmpeg_dir);
  std::shared_ptr<te::FfmpegBridge> ffmpeg;
  if (ffmpeg_dir) {
    ffmpeg = std::make_shared<te::FfmpegBridge>(*ffmpeg_dir, app.hls);
    std::cerr << "[engine] ffmpeg dir: " << ffmpeg_dir->string() << "\n";
  } else {
    std::cerr << "[engine] warning: ffmpeg/ffprobe not found — tracks/hls disabled\n";
    ffmpeg = std::make_shared<te::FfmpegBridge>(std::filesystem::path{}, app.hls);
  }

  auto torrents = std::make_shared<te::TorrentManager>(app);
  // Piece-wait callback for stdin pipe HLS (MKV/AVI/TS).
  ffmpeg->set_wait_range(
      [torrents](const std::string& id, std::int64_t start, std::int64_t end,
                 int timeout_ms) {
        return torrents->wait_media_range(id, start, end, timeout_ms);
      });

  std::atomic<bool> running{true};
  std::thread alert_thread([&] {
    while (running.load()) {
      torrents->tick();
      std::this_thread::sleep_for(std::chrono::milliseconds(200));
    }
  });

  if (app.update.check_on_start) {
    auto url = te::resolve_manifest_url(app.update.manifest_url);
    if (!url.empty()) {
      auto chk = te::check_for_update(url);
      if (chk.ok && chk.update_available) {
        std::cerr << "[engine] update available: " << chk.current_version
                  << " → " << chk.latest_version
                  << " (run: torrent-engine --update)\n";
      } else if (!chk.ok) {
        std::cerr << "[engine] update check skipped: " << chk.error << "\n";
      }
    }
  }

  te::EngineConfig http_cfg;
  http_cfg.port = app.port;
  http_cfg.bind = app.bind;
  http_cfg.data_dir = app.data_dir;
  http_cfg.ffmpeg_dir = app.ffmpeg_dir;
  http_cfg.update_manifest_url = app.update.manifest_url;

  te::HttpServer server(http_cfg, torrents, ffmpeg,
                        std::make_shared<te::PartyManager>());
  g_server = &server;
  std::signal(SIGINT, on_signal);
  std::signal(SIGTERM, on_signal);

  server.run();

  running = false;
  if (alert_thread.joinable()) alert_thread.join();
  return 0;
}
