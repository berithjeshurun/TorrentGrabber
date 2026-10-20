#pragma once

#include "ffmpeg_bridge.hpp"
#include "party_manager.hpp"
#include "torrent_manager.hpp"

#include <memory>
#include <mutex>
#include <string>
#include <unordered_map>

namespace te {

struct EngineConfig {
  int port = 0;  // 0 = ephemeral
  std::string bind = "127.0.0.1";
  std::filesystem::path data_dir = "engine-data";
  std::optional<std::filesystem::path> ffmpeg_dir;
  std::string update_manifest_url;
};

struct CastSession {
  std::string token;
  std::string download_id;
  std::string mode;  // export | hls
  std::string file;  // export filename or playlist name
  std::int64_t expires_ms = 0;
};

class HttpServer {
 public:
  HttpServer(EngineConfig cfg,
             std::shared_ptr<TorrentManager> torrents,
             std::shared_ptr<FfmpegBridge> ffmpeg,
             std::shared_ptr<PartyManager> parties = nullptr);
  ~HttpServer();

  // Blocking serve. Prints ENGINE_READY port=N then listens.
  void run();
  void stop();
  int port() const { return port_; }

 private:
  void setup_routes();

  EngineConfig cfg_;
  std::shared_ptr<TorrentManager> torrents_;
  std::shared_ptr<FfmpegBridge> ffmpeg_;
  std::shared_ptr<PartyManager> parties_;
  int port_ = 0;
  void* server_ = nullptr;  // httplib::Server*

  std::mutex cast_mu_;
  std::unordered_map<std::string, CastSession> cast_sessions_;
  std::int64_t metrics_hls_starts_ = 0;
  std::int64_t metrics_hls_gate_ = 0;
  std::int64_t metrics_hls_errors_ = 0;
};

// Serve Range media for a torrent. Returns true if response written.
bool serve_media_range(TorrentManager& tm,
                       const std::string& id,
                       const std::string& range_header,
                       void* req,
                       void* res);

}  // namespace te
