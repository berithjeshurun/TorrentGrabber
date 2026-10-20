#pragma once

#include "config.hpp"
#include "modes.hpp"

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include <libtorrent/session.hpp>
#include <libtorrent/torrent_handle.hpp>
#include <nlohmann/json.hpp>

namespace te {

struct TorrentJob {
  std::string id;
  std::string magnet_or_url;
  DownloadMode mode = DownloadMode::General;
  ModeProfile profile;
  lt::torrent_handle handle;
  int media_file_index = -1;
  std::int64_t playhead_bytes = 0;
  std::string error;
  bool paused = false;
  std::filesystem::path torrent_file;  // .torrent on disk for share jobs

  // Streaming control (3rd-party controllable)
  double boost_mult = 1.0;           // multiplies lookahead while active
  std::int64_t boost_until_ms = 0;   // epoch ms; 0 = inactive
  int abr_height = 0;                // 0 = auto; else preferred ladder height
  double underrun_threshold_sec = 3.0;
  // TorrServer-style: re-arm piece priorities while a stream is active.
  std::int64_t last_policy_ms = 0;
};

class TorrentManager {
 public:
  explicit TorrentManager(AppConfig cfg);
  ~TorrentManager();

  TorrentManager(const TorrentManager&) = delete;
  TorrentManager& operator=(const TorrentManager&) = delete;

  void tick();

  nlohmann::json add(const std::string& magnet_or_url,
                     DownloadMode mode,
                     const std::vector<int>& selective_files = {},
                     const std::optional<std::string>& id = std::nullopt,
                     const std::vector<std::pair<std::string, int>>& peers = {});

  // Create a (private) torrent from a local file/folder and seed it.
  nlohmann::json share_path(const std::filesystem::path& path,
                            bool make_private,
                            const std::optional<std::string>& name,
                            const std::optional<std::string>& comment,
                            const std::optional<std::string>& id = std::nullopt);

  bool add_peer(const std::string& id, const std::string& ip, int port);

  nlohmann::json status(const std::string& id);
  nlohmann::json list();
  bool pause(const std::string& id);
  bool resume(const std::string& id);
  bool remove(const std::string& id, bool delete_files);

  bool set_playhead(const std::string& id, std::int64_t offset_bytes);

  // Expand lookahead temporarily (boost ahead). duration_sec, multiplier (>=1).
  nlohmann::json boost(const std::string& id, double duration_sec, double multiplier);

  // Preferred ABR ladder height (0 = auto). Clients should honor; engine stores.
  nlohmann::json set_abr(const std::string& id, int height);

  // Friend invite payload for QR / deep links (magnet + dial_endpoint).
  nlohmann::json invite(const std::string& id);

  // Buffer / underrun snapshot relative to playhead windows.
  nlohmann::json buffer_health(const std::string& id);

  // Contiguous downloaded bytes from `from_byte` within the media file.
  std::int64_t sequential_bytes(const std::string& id, std::int64_t from_byte = 0);

  // HLS readiness: sequential gate + container policy. Soft-fail with retry=true.
  nlohmann::json hls_readiness(const std::string& id,
                               std::int64_t from_byte = 0,
                               std::int64_t min_bytes = 0);

  // Wait until [start,end] pieces of the media file are available.
  bool wait_media_range(const std::string& id,
                        std::int64_t start,
                        std::int64_t end,
                        int timeout_ms = 20000);

  // Switch which file is considered the streaming media file (selective prioritize).
  bool set_media_file(const std::string& id, int file_index);

  // Suggest ABR height from live download rate (0 = keep auto).
  int suggest_abr_height(const std::string& id);

  std::shared_ptr<TorrentJob> get(const std::string& id);
  std::filesystem::path data_dir() const { return data_dir_; }
  const AppConfig& config() const { return cfg_; }

  nlohmann::json session_stats();
  nlohmann::json metrics_snapshot();

  void note_underrun();
  void note_hls_restart();
  void note_piece_wait(std::int64_t waited_ms, bool ok);

  std::optional<std::filesystem::path> media_path(const std::string& id);
  std::optional<std::filesystem::path> torrent_file_path(const std::string& id);

  void apply_stream_policy(TorrentJob& job);

  int listen_port() const;

 private:
  void configure_session(DownloadMode hint);
  void on_metadata(TorrentJob& job);
  int pick_media_file(const lt::torrent_handle& h) const;
  nlohmann::json job_to_json(const TorrentJob& job);
  nlohmann::json compute_buffer_locked(const TorrentJob& job) const;
  std::string magnet_with_peers(const std::string& base_magnet) const;
  void apply_private_flags(lt::add_torrent_params& atp, const ModeProfile& profile) const;

  AppConfig cfg_;
  std::filesystem::path data_dir_;
  std::unique_ptr<lt::session> session_;
  std::mutex mu_;
  std::unordered_map<std::string, std::shared_ptr<TorrentJob>> jobs_;

  std::atomic<std::int64_t> metric_underruns_{0};
  std::atomic<std::int64_t> metric_hls_restarts_{0};
  std::atomic<std::int64_t> metric_piece_waits_{0};
  std::atomic<std::int64_t> metric_piece_wait_ok_{0};
  std::atomic<std::int64_t> metric_piece_wait_ms_sum_{0};
};

}  // namespace te
