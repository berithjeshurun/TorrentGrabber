#pragma once

#include "config.hpp"
#include "torrent_manager.hpp"

#include <atomic>
#include <cstdint>
#include <filesystem>
#include <functional>
#include <memory>
#include <mutex>
#include <optional>
#include <string>
#include <thread>
#include <unordered_map>
#include <vector>

#include <nlohmann/json.hpp>

namespace te {

// Per-request HLS options (API overrides layered on HlsConfig).
struct HlsRequest {
  std::optional<int> audio_index;
  std::optional<double> start_seconds;
  bool audio_only = false;
  std::optional<int> burn_subtitle;  // absolute stream index to burn in
  std::optional<std::string> audio_bitrate;
  std::optional<std::string> audio_channels;
  std::optional<bool> loudnorm;
  std::optional<std::string> deinterlace;
  std::optional<std::string> tonemap;
  std::optional<std::string> lut_cube;
  std::optional<bool> abr;
  // Prefer HTTP /media URL so ffmpeg reads wait on torrent pieces (sparse files).
  std::optional<std::string> input_url;
  // When true, try stdin pipe from piece-waited reader (MKV/AVI/TS).
  bool prefer_pipe = false;
  // Cap ABR ladder height (0 = auto).
  int max_height = 0;
};

class FfmpegBridge {
 public:
  FfmpegBridge(std::filesystem::path ffmpeg_dir, HlsConfig hls_cfg);

  bool available() const;
  const std::filesystem::path& dir() const { return dir_; }
  const HlsConfig& hls_config() const { return hls_; }
  void set_hls_config(HlsConfig cfg) { hls_ = std::move(cfg); }

  // Optional piece-wait callback for stdin pipe HLS (download_id, start, end, timeout_ms).
  using WaitRangeFn =
      std::function<bool(const std::string&, std::int64_t, std::int64_t, int)>;
  void set_wait_range(WaitRangeFn fn) { wait_range_ = std::move(fn); }

  nlohmann::json probe(const std::filesystem::path& media);

  nlohmann::json ensure_hls(const std::string& download_id,
                            const std::filesystem::path& media,
                            const HlsRequest& req,
                            const std::filesystem::path& work_root);

  nlohmann::json set_subtitle(const std::string& download_id,
                              const std::filesystem::path& media,
                              std::optional<int> subtitle_index,
                              const std::filesystem::path& work_root);

  nlohmann::json ensure_poster(const std::string& download_id,
                               const std::filesystem::path& media,
                               const std::filesystem::path& work_root);

  nlohmann::json ensure_thumbs(const std::string& download_id,
                               const std::filesystem::path& media,
                               const std::filesystem::path& work_root,
                               int interval_sec = 10);

  // Phase 2 — offline / library tooling
  nlohmann::json export_clip(const std::string& download_id,
                             const std::filesystem::path& media,
                             const std::filesystem::path& work_root,
                             double start_sec,
                             double end_sec,
                             const std::string& container = "mp4");

  nlohmann::json remux_select(const std::string& download_id,
                              const std::filesystem::path& media,
                              const std::filesystem::path& work_root,
                              const std::string& container,
                              std::optional<int> video_index,
                              std::optional<int> audio_index,
                              std::optional<int> subtitle_index);

  nlohmann::json cast_export(const std::string& download_id,
                             const std::filesystem::path& media,
                             const std::filesystem::path& work_root,
                             const std::string& profile);  // chromecast | airplay

  nlohmann::json waveform(const std::string& download_id,
                          const std::filesystem::path& media,
                          const std::filesystem::path& work_root);

  nlohmann::json scene_chapters(const std::string& download_id,
                                const std::filesystem::path& media,
                                const std::filesystem::path& work_root,
                                double threshold = 0.35);

  std::optional<std::filesystem::path> hls_file(
      const std::string& download_id,
      const std::string& filename,
      const std::filesystem::path& work_root);

  std::optional<std::filesystem::path> asset_file(
      const std::string& download_id,
      const std::string& kind,
      const std::string& filename,
      const std::filesystem::path& work_root);

  std::optional<std::filesystem::path> export_file(
      const std::string& download_id,
      const std::string& filename,
      const std::filesystem::path& work_root);

 private:
  struct ResolvedHls {
    std::string audio_bitrate;
    std::string audio_channels;
    int hls_time = 2;
    bool low_latency = true;
    bool loudnorm = false;
    std::string deinterlace;
    std::string tonemap;
    std::string lut_cube;
    bool abr = false;
    bool audio_only = false;
    std::optional<int> audio_index;
    std::optional<double> start_seconds;
    std::optional<int> burn_subtitle;
    bool force_transcode = false;
    bool apply_yadif = false;
    bool apply_tonemap = false;
    std::string tonemap_algo = "hable";
    std::string color_transfer;
    std::string color_primaries;
    std::string color_space;
    int src_height = 0;
    double duration = 0;
    std::string input_url;  // optional HTTP input for piece-aware reads
    bool prefer_pipe = false;
    int max_height = 0;
    bool video_hevc = false;
    bool use_fmp4 = false;
    bool copy_audio = false;
  };

  struct HlsSession {
    std::string download_id;
    std::filesystem::path work_dir;
    std::optional<int> audio_index;
    std::optional<int> subtitle_index;
    std::optional<double> start_seconds;
    bool audio_only = false;
    bool abr = false;
    std::optional<int> burn_subtitle;
    std::string options_key;
    std::filesystem::path playlist;       // index.m3u8 or master.m3u8
    std::filesystem::path subtitle_vtt;
    std::filesystem::path poster;
    std::filesystem::path thumbs_dir;
    std::filesystem::path exports_dir;
    void* video_proc = nullptr;
    void* stdin_write = nullptr;  // HANDLE (Windows) for pipe:0 feeder
    std::shared_ptr<std::atomic<bool>> pipe_stop;
    std::thread pipe_thread;
    std::uint64_t media_bytes = 0;
    bool finalized = false;
    bool use_fmp4 = false;
  };

  HlsSession& ensure_session_locked(const std::string& download_id,
                                    const std::filesystem::path& work_root);
  std::filesystem::path tool_bin(const char* name) const;
  bool run_tool(const std::vector<std::string>& args,
                std::string* err_out,
                int timeout_ms = 120000);
  nlohmann::json silence_detect(const std::filesystem::path& media);

  ResolvedHls resolve(const HlsRequest& req, const nlohmann::json& probe_j) const;
  std::string options_key(const ResolvedHls& r) const;
  std::string build_vf(const ResolvedHls& r,
                       const std::filesystem::path& media) const;
  std::string build_af(const ResolvedHls& r) const;
  static std::string escape_lut_path(const std::string& path);

  bool run_ffmpeg(const std::vector<std::string>& args,
                  std::string* err_out,
                  int timeout_ms = 120000);
  bool spawn_hls(HlsSession& sess,
                 const std::filesystem::path& media,
                 const ResolvedHls& opts,
                 bool transcode,
                 std::string* err);
  bool spawn_hls_abr(HlsSession& sess,
                     const std::filesystem::path& media,
                     const ResolvedHls& opts,
                     std::string* err);

  void kill_proc(void*& handle);
  void stop_pipe_feeder(HlsSession& sess);
  static bool proc_alive(void* handle);
  void finalize_playlist_if_dead(HlsSession& sess);
  nlohmann::json session_json(const HlsSession& sess) const;
  bool start_pipe_feeder(HlsSession& sess,
                         const std::filesystem::path& media,
                         std::int64_t start_offset);

  std::filesystem::path dir_;
  HlsConfig hls_;
  std::mutex mu_;
  std::unordered_map<std::string, HlsSession> sessions_;
  WaitRangeFn wait_range_;
};

}  // namespace te
