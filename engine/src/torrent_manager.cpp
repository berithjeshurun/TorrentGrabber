#include "torrent_manager.hpp"
#include "util.hpp"

#include <algorithm>
#include <chrono>
#include <cstring>
#include <iostream>
#include <thread>

#include <libtorrent/add_torrent_params.hpp>
#include <libtorrent/address.hpp>
#include <libtorrent/alert_types.hpp>
#include <libtorrent/create_torrent.hpp>
#include <libtorrent/load_torrent.hpp>
#include <libtorrent/magnet_uri.hpp>
#include <libtorrent/torrent_info.hpp>
#include <libtorrent/torrent_status.hpp>

#include <fstream>

namespace te {
namespace {

lt::storage_mode_t storage_mode() { return lt::storage_mode_sparse; }

}  // namespace

TorrentManager::TorrentManager(AppConfig cfg)
    : cfg_(std::move(cfg)), data_dir_(cfg_.data_dir) {
  std::filesystem::create_directories(data_dir_);

  if (cfg_.share.public_host.empty() && cfg_.share.auto_detect_public_host) {
    auto ip = detect_public_ipv4(5);
    if (!ip.empty()) {
      cfg_.share.public_host = ip;
      std::cerr << "[engine] share.public_host auto-detected: " << ip << "\n";
    } else {
      std::cerr << "[engine] share.auto_detect_public_host failed "
                   "(set share.public_host manually)\n";
    }
  }

  lt::settings_pack pack;
  pack.set_str(lt::settings_pack::user_agent, "torrent-engine/0.1");
  pack.set_bool(lt::settings_pack::enable_dht, true);
  pack.set_bool(lt::settings_pack::enable_lsd, true);
  pack.set_bool(lt::settings_pack::enable_upnp, true);
  pack.set_bool(lt::settings_pack::enable_natpmp, true);
  pack.set_bool(lt::settings_pack::enable_outgoing_utp, true);
  pack.set_bool(lt::settings_pack::enable_incoming_utp, true);
  pack.set_bool(lt::settings_pack::enable_outgoing_tcp, true);
  pack.set_bool(lt::settings_pack::enable_incoming_tcp, true);
  pack.set_int(lt::settings_pack::connections_limit, cfg_.session.connections_limit);
  pack.set_int(lt::settings_pack::active_downloads, cfg_.session.active_downloads);
  pack.set_int(lt::settings_pack::active_seeds, cfg_.session.active_seeds);
  pack.set_int(lt::settings_pack::active_limit, cfg_.session.active_limit);
  if (cfg_.share.listen_port > 0) {
    pack.set_str(lt::settings_pack::listen_interfaces,
                 "0.0.0.0:" + std::to_string(cfg_.share.listen_port));
  }
  pack.set_int(lt::settings_pack::alert_mask,
               lt::alert_category::error | lt::alert_category::status |
                   lt::alert_category::storage | lt::alert_category::piece_progress);

  session_ = std::make_unique<lt::session>(pack);
}

TorrentManager::~TorrentManager() {
  if (session_) {
    session_->pause();
  }
}

void TorrentManager::configure_session(DownloadMode hint) {
  auto profile = profile_for(hint, cfg_);
  lt::settings_pack pack;
  pack.set_int(lt::settings_pack::connections_limit, profile.connections_limit);
  pack.set_int(lt::settings_pack::active_downloads, profile.active_downloads);
  if (profile.download_rate_limit > 0)
    pack.set_int(lt::settings_pack::download_rate_limit, profile.download_rate_limit);
  else
    pack.set_int(lt::settings_pack::download_rate_limit, 0);
  if (profile.upload_rate_limit > 0)
    pack.set_int(lt::settings_pack::upload_rate_limit, profile.upload_rate_limit);
  else
    pack.set_int(lt::settings_pack::upload_rate_limit, 0);
  session_->apply_settings(pack);
}

void TorrentManager::tick() {
  std::vector<lt::alert*> alerts;
  session_->pop_alerts(&alerts);
  for (lt::alert* a : alerts) {
    if (auto* m = lt::alert_cast<lt::metadata_received_alert>(a)) {
      std::lock_guard lock(mu_);
      for (auto& [id, job] : jobs_) {
        if (job->handle == m->handle) {
          on_metadata(*job);
          break;
        }
      }
    } else if (auto* e = lt::alert_cast<lt::torrent_error_alert>(a)) {
      std::lock_guard lock(mu_);
      for (auto& [id, job] : jobs_) {
        if (job->handle == e->handle) {
          job->error = e->error.message();
          break;
        }
      }
    }
  }

  // Priority watchdog (TorrServer-inspired): re-arm stream bands every ~2s
  // so piece priorities never go stale while the player is reading.
  const auto now_ms = std::chrono::duration_cast<std::chrono::milliseconds>(
                          std::chrono::system_clock::now().time_since_epoch())
                          .count();
  std::lock_guard lock(mu_);
  for (auto& [id, job] : jobs_) {
    (void)id;
    if (!job || job->paused || !job->handle.is_valid()) continue;
    if (job->mode != DownloadMode::Stream && job->mode != DownloadMode::Preview &&
        job->mode != DownloadMode::Fastest && job->mode != DownloadMode::Mirror)
      continue;
    if (job->media_file_index < 0) continue;
    if (job->last_policy_ms > 0 && (now_ms - job->last_policy_ms) < 2000) continue;
    apply_stream_policy(*job);
  }
}

void TorrentManager::on_metadata(TorrentJob& job) {
  if (!job.handle.is_valid()) return;
  auto ti = job.handle.torrent_file();
  if (!ti) return;

  job.media_file_index = pick_media_file(job.handle);
  const int n = ti->num_files();
  std::vector<lt::download_priority_t> prios(static_cast<size_t>(n),
                                             lt::dont_download);

  if (job.mode == DownloadMode::Selective && !job.profile.selective_files.empty()) {
    for (int idx : job.profile.selective_files) {
      if (idx >= 0 && idx < n) prios[static_cast<size_t>(idx)] = lt::default_priority;
    }
  } else if (job.mode == DownloadMode::Stream || job.mode == DownloadMode::Preview ||
             job.mode == DownloadMode::Fastest || job.mode == DownloadMode::Mirror) {
    if (job.media_file_index >= 0)
      prios[static_cast<size_t>(job.media_file_index)] = lt::top_priority;
  } else {
    std::fill(prios.begin(), prios.end(), lt::default_priority);
  }

  job.handle.prioritize_files(prios);

  if (job.profile.sequential) {
    job.handle.set_flags(lt::torrent_flags::sequential_download);
  }

  if (job.mode == DownloadMode::Stream || job.mode == DownloadMode::Preview) {
    apply_stream_policy(job);
  }
}

int TorrentManager::pick_media_file(const lt::torrent_handle& h) const {
  auto ti = h.torrent_file();
  if (!ti) return -1;
  int best = -1;
  std::int64_t best_size = -1;
  for (int i = 0; i < ti->num_files(); ++i) {
    auto name = std::string(ti->layout().file_name(lt::file_index_t{i}));
    auto sz = ti->layout().file_size(lt::file_index_t{i});
    if (looks_like_video(name) && sz > best_size) {
      best = i;
      best_size = sz;
    }
  }
  if (best >= 0) return best;
  // fallback: largest file
  for (int i = 0; i < ti->num_files(); ++i) {
    auto sz = ti->layout().file_size(lt::file_index_t{i});
    if (sz > best_size) {
      best = i;
      best_size = sz;
    }
  }
  return best;
}

void TorrentManager::apply_stream_policy(TorrentJob& job) {
  if (!job.handle.is_valid()) return;
  auto ti = job.handle.torrent_file();
  if (!ti || job.media_file_index < 0) return;

  const auto& fs = ti->layout();
  const auto file_idx = lt::file_index_t{job.media_file_index};
  const std::int64_t file_size = fs.file_size(file_idx);
  const std::int64_t file_off = fs.file_offset(file_idx);
  const int piece_length = ti->piece_length();
  if (piece_length <= 0 || file_size <= 0) return;

  const int num_pieces = ti->num_pieces();
  // TorrServer tiers mapped to libtorrent 0–7 priorities:
  //   NOW(7) → NEXT(6) → READAHEAD(5) → HIGH(4) → SPEC(2) → NONE(0)
  constexpr auto prio_now = lt::top_priority;                       // 7
  constexpr auto prio_next = lt::download_priority_t{6};
  constexpr auto prio_readahead = lt::download_priority_t{5};
  constexpr auto prio_high = lt::default_priority;                  // 4
  constexpr auto prio_spec = lt::download_priority_t{2};
  std::vector<lt::download_priority_t> piece_prios(static_cast<size_t>(num_pieces),
                                                   lt::dont_download);

  auto mark_range = [&](std::int64_t start_b, std::int64_t end_b,
                        lt::download_priority_t prio, bool overwrite = true) {
    start_b = std::max<std::int64_t>(0, start_b);
    end_b = std::min(file_size, end_b);
    if (start_b >= end_b) return;
    const int first = static_cast<int>((file_off + start_b) / piece_length);
    const int last = static_cast<int>((file_off + end_b - 1) / piece_length);
    for (int i = first; i <= last && i < num_pieces; ++i) {
      if (i < 0) continue;
      auto& slot = piece_prios[static_cast<size_t>(i)];
      if (!overwrite && slot != lt::dont_download) continue;
      // Never demote a hotter band when overlapping.
      if (static_cast<std::uint8_t>(slot) > static_cast<std::uint8_t>(prio))
        continue;
      slot = prio;
    }
  };

  auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
                 std::chrono::system_clock::now().time_since_epoch())
                 .count();
  double boost = 1.0;
  if (job.boost_until_ms > now && job.boost_mult > 1.0) boost = job.boost_mult;
  job.last_policy_ms = now;

  job.handle.clear_piece_deadlines();

  // Probe head/tail so ffprobe / container headers always arrive first.
  mark_range(0, std::min<std::int64_t>(file_size, 2LL * 1024 * 1024), prio_now);
  if (file_size > 4LL * 1024 * 1024)
    mark_range(file_size - 1LL * 1024 * 1024, file_size, prio_high, false);

  if (job.mode == DownloadMode::Preview) {
    mark_range(0, job.profile.preview_head_bytes, prio_now);
    mark_range(file_size - job.profile.preview_tail_bytes, file_size, prio_now);
  } else {
    const auto ph = job.playhead_bytes;
    const auto urgent = job.profile.playhead_window_bytes;
    const auto look =
        static_cast<std::int64_t>(job.profile.lookahead_bytes * boost);
    // NOW: first ~2 pieces after playhead
    const std::int64_t now_bytes =
        std::min<std::int64_t>(urgent, 2LL * piece_length);
    // NEXT: rest of urgent window
    // READAHEAD: lookahead band
    // HIGH: short shoulder past lookahead
    // SPEC: sparse samples further ahead (anti-stall for seeks)
    mark_range(ph, ph + now_bytes, prio_now);
    mark_range(ph + now_bytes, ph + urgent, prio_next);
    mark_range(ph + urgent, ph + urgent + look, prio_readahead);
    mark_range(ph + urgent + look,
               ph + urgent + look + 8LL * 1024 * 1024, prio_high);

    const std::int64_t spec_start = ph + urgent + look + 8LL * 1024 * 1024;
    if (spec_start < file_size) {
      const std::int64_t stride = std::max<std::int64_t>(
          piece_length * 8, 4LL * 1024 * 1024);
      for (std::int64_t off = spec_start; off < file_size; off += stride) {
        mark_range(off, off + piece_length, prio_spec, false);
      }
    }
  }

  job.handle.prioritize_pieces(piece_prios);

  // Deadline gradient: hotter pieces get tighter soft deadlines.
  const auto ph = job.mode == DownloadMode::Preview ? 0 : job.playhead_bytes;
  const int first = static_cast<int>((file_off + ph) / piece_length);
  const std::int64_t want = std::max<std::int64_t>(
      48LL * 1024 * 1024,
      static_cast<std::int64_t>(job.profile.playhead_window_bytes +
                                job.profile.lookahead_bytes * 0.5));
  int deadline_n = static_cast<int>((want + piece_length - 1) / piece_length);
  if (deadline_n < 6) deadline_n = 6;
  if (deadline_n > 48) deadline_n = 48;
  for (int k = 0; k < deadline_n; ++k) {
    int i = first + k;
    if (i < 0 || i >= num_pieces) break;
    auto p = piece_prios[static_cast<size_t>(i)];
    if (p == lt::dont_download) continue;
    int deadline = 200 + k * 120;
    if (static_cast<std::uint8_t>(p) >= 6) deadline = 80 + k * 60;
    else if (static_cast<std::uint8_t>(p) >= 5) deadline = 150 + k * 90;
    job.handle.set_piece_deadline(lt::piece_index_t{i}, deadline);
  }
}

nlohmann::json TorrentManager::add(
    const std::string& magnet_or_url,
    DownloadMode mode,
    const std::vector<int>& selective_files,
    const std::optional<std::string>& id,
    const std::vector<std::pair<std::string, int>>& peers) {
  // Fetch remote .torrent outside the session lock (network I/O).
  std::string http_torrent;
  if (looks_like_http_url(magnet_or_url)) {
    std::string err;
    auto body = http_get_bytes(magnet_or_url, &err, 90);
    if (!body) {
      return nlohmann::json{{"error", err.empty() ? "torrent URL fetch failed" : err}};
    }
    if (body->empty() || (*body)[0] != 'd') {
      return nlohmann::json{{"error", "URL did not return a .torrent payload"}};
    }
    http_torrent = std::move(*body);
  }

  std::lock_guard lock(mu_);
  configure_session(mode);

  auto job = std::make_shared<TorrentJob>();
  job->id = id.value_or(make_id());
  job->magnet_or_url = magnet_or_url;
  job->mode = mode;
  job->profile = profile_for(mode, cfg_);
  job->profile.selective_files = selective_files;

  lt::add_torrent_params atp;
  atp.save_path = data_dir_.string();
  atp.storage_mode = storage_mode();

  lt::error_code ec;
  if (magnet_or_url.rfind("magnet:", 0) == 0) {
    atp = lt::parse_magnet_uri(magnet_or_url, ec);
    if (ec) {
      nlohmann::json err;
      err["error"] = ec.message();
      return err;
    }
    atp.save_path = data_dir_.string();
  } else if (!http_torrent.empty()) {
    atp = lt::load_torrent_buffer(
        lt::span<char const>(http_torrent.data(),
                             static_cast<std::ptrdiff_t>(http_torrent.size())),
        ec, lt::load_torrent_limits{});
    if (ec) {
      nlohmann::json err;
      err["error"] = ec.message();
      return err;
    }
    atp.save_path = data_dir_.string();
    atp.storage_mode = storage_mode();
  } else if (std::filesystem::exists(magnet_or_url)) {
    atp = lt::load_torrent_file(magnet_or_url, ec, lt::load_torrent_limits{});
    if (ec) {
      nlohmann::json err;
      err["error"] = ec.message();
      return err;
    }
    atp.save_path = data_dir_.string();
    atp.storage_mode = storage_mode();
  } else {
    nlohmann::json err;
    err["error"] =
        "expected magnet:, http(s)://...torrent, or local .torrent path";
    return err;
  }

  atp.flags &= ~lt::torrent_flags::paused;
  atp.flags &= ~lt::torrent_flags::auto_managed;
  atp.save_path = data_dir_.string();
  atp.storage_mode = storage_mode();
  apply_private_flags(atp, job->profile);

  if (job->profile.sequential) {
    atp.flags |= lt::torrent_flags::sequential_download;
  }

  for (auto& [ip, port] : peers) {
    lt::error_code pec;
    auto addr = lt::make_address(ip, pec);
    if (!pec) atp.peers.emplace_back(addr, static_cast<std::uint16_t>(port));
  }

  try {
    job->handle = session_->add_torrent(std::move(atp));
  } catch (const std::exception& ex) {
    nlohmann::json err;
    err["error"] = ex.what();
    return err;
  }

  if (job->handle.torrent_file()) {
    on_metadata(*job);
  }

  jobs_[job->id] = job;
  return job_to_json(*job);
}

void TorrentManager::apply_private_flags(lt::add_torrent_params& atp,
                                         const ModeProfile& profile) const {
  if (!profile.private_swarm) return;
  atp.flags |= lt::torrent_flags::disable_dht;
  atp.flags |= lt::torrent_flags::disable_lsd;
  atp.flags |= lt::torrent_flags::disable_pex;
}

std::string TorrentManager::magnet_with_peers(const std::string& base_magnet) const {
  if (cfg_.share.public_host.empty()) return base_magnet;
  int port = listen_port();
  if (port <= 0) port = cfg_.share.listen_port;
  if (port <= 0) return base_magnet;
  std::string m = base_magnet;
  if (m.find('&') == std::string::npos && m.find('?') != std::string::npos) {
    // ok
  }
  m += "&x.pe=";
  m += cfg_.share.public_host;
  m += ":";
  m += std::to_string(port);
  return m;
}

nlohmann::json TorrentManager::share_path(
    const std::filesystem::path& path,
    bool make_private,
    const std::optional<std::string>& name,
    const std::optional<std::string>& comment,
    const std::optional<std::string>& id) {
  std::lock_guard lock(mu_);
  std::error_code fec;
  auto abs = std::filesystem::absolute(path, fec);
  if (fec || !std::filesystem::exists(abs)) {
    return nlohmann::json{{"error", "path not found"}};
  }

  configure_session(DownloadMode::Friend);

  auto files = lt::list_files(abs.string());
  if (files.empty()) {
    return nlohmann::json{{"error", "no files to share"}};
  }

  lt::create_torrent ct(std::move(files), 0, lt::create_torrent::v1_only);
  ct.set_creator("torrent-engine");
  if (make_private) ct.set_priv(true);
  if (comment) ct.set_comment(comment->c_str());

  // Root for hashing: parent of the listed top-level entry.
  std::filesystem::path root =
      std::filesystem::is_directory(abs) ? abs.parent_path() : abs.parent_path();
  if (root.empty()) root = abs.root_path();

  lt::error_code ec;
  lt::set_piece_hashes(ct, root.string(), ec);
  if (ec) {
    return nlohmann::json{{"error", std::string("hash failed: ") + ec.message()}};
  }

  auto buf = ct.generate_buf();
  auto job = std::make_shared<TorrentJob>();
  job->id = id.value_or(make_id());
  job->mode = DownloadMode::Friend;
  job->profile = profile_for(DownloadMode::Friend, cfg_);
  job->profile.private_swarm = make_private;

  auto share_dir = data_dir_ / "shares";
  std::filesystem::create_directories(share_dir);
  job->torrent_file = share_dir / (job->id + ".torrent");
  {
    std::ofstream out(job->torrent_file, std::ios::binary);
    out.write(buf.data(), static_cast<std::streamsize>(buf.size()));
  }

  lt::error_code lec;
  lt::add_torrent_params atp = lt::load_torrent_buffer(
      lt::span<char const>(buf.data(), static_cast<std::ptrdiff_t>(buf.size())),
      lec, lt::load_torrent_limits{});
  if (lec || !atp.ti) {
    return nlohmann::json{
        {"error", lec ? lec.message() : "failed to parse created torrent"}};
  }

  atp.save_path = root.string();
  atp.storage_mode = storage_mode();
  atp.flags &= ~lt::torrent_flags::paused;
  atp.flags &= ~lt::torrent_flags::auto_managed;
  atp.flags |= lt::torrent_flags::seed_mode;
  apply_private_flags(atp, job->profile);

  std::string magnet = lt::make_magnet_uri(atp);
  magnet = magnet_with_peers(magnet);
  job->magnet_or_url = magnet;

  try {
    job->handle = session_->add_torrent(std::move(atp));
  } catch (const std::exception& ex) {
    return nlohmann::json{{"error", ex.what()}};
  }

  if (job->handle.torrent_file()) on_metadata(*job);

  jobs_[job->id] = job;
  auto j = job_to_json(*job);
  j["magnet"] = magnet;
  j["private"] = make_private;
  j["torrent_url"] = "/v1/torrents/" + job->id + "/torrent";
  j["listen_port"] = listen_port();
  j["public_host"] = cfg_.share.public_host.empty() ? nullptr
                                                    : nlohmann::json(cfg_.share.public_host);
  if (!cfg_.share.public_host.empty() && listen_port() > 0) {
    j["dial_endpoint"] =
        cfg_.share.public_host + ":" + std::to_string(listen_port());
  } else {
    j["dial_endpoint"] = nullptr;
  }
  j["share_path"] = abs.string();
  if (name) j["share_name"] = *name;
  j["howto"] =
      "Internet share uses the BitTorrent listen port (share.listen_port), not the "
      "HTTP API. Set share.public_host (or auto_detect_public_host), forward that "
      "UDP/TCP port (or use Tailscale/UPnP), then send magnet (includes x.pe) to "
      "your friend. They POST /v1/torrents with mode=friend; dial via x.pe or "
      "POST /peers with dial_endpoint. Keep HTTP bind on 127.0.0.1.";
  return j;
}

bool TorrentManager::add_peer(const std::string& id, const std::string& ip, int port) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end() || !it->second->handle.is_valid()) return false;
  lt::error_code ec;
  auto addr = lt::make_address(ip, ec);
  if (ec) return false;
  it->second->handle.connect_peer(
      lt::tcp::endpoint(addr, static_cast<std::uint16_t>(port)));
  return true;
}

int TorrentManager::listen_port() const {
  if (!session_) return cfg_.share.listen_port;
  return session_->listen_port();
}

std::optional<std::filesystem::path> TorrentManager::torrent_file_path(
    const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return std::nullopt;
  if (it->second->torrent_file.empty() ||
      !std::filesystem::exists(it->second->torrent_file))
    return std::nullopt;
  return it->second->torrent_file;
}

nlohmann::json TorrentManager::job_to_json(const TorrentJob& job) {
  nlohmann::json j;
  j["id"] = job.id;
  j["magnet_or_url"] = job.magnet_or_url;
  j["mode"] = mode_name(job.mode);
  j["error"] = job.error.empty() ? nullptr : nlohmann::json(job.error);
  j["playhead"] = job.playhead_bytes;
  j["media_file_index"] = job.media_file_index;

  if (!job.handle.is_valid()) {
    j["status"] = "error";
    j["name"] = nullptr;
    j["progress"] = 0.0;
    j["total_bytes"] = 0;
    j["downloaded_bytes"] = 0;
    j["download_rate"] = 0;
    j["upload_rate"] = 0;
    j["num_peers"] = 0;
    return j;
  }

  auto st = job.handle.status();
  j["name"] = st.name;
  j["info_hash"] = "";
  if (st.info_hashes.has_v1()) {
    auto const& h = st.info_hashes.v1;
    static const char* hexd = "0123456789abcdef";
    std::string hex;
    hex.resize(40);
    for (int i = 0; i < 20; ++i) {
      auto b = static_cast<unsigned char>(h[i]);
      hex[static_cast<size_t>(i * 2)] = hexd[b >> 4];
      hex[static_cast<size_t>(i * 2 + 1)] = hexd[b & 0xf];
    }
    j["info_hash"] = hex;
  }
  j["progress"] = st.progress;
  j["total_bytes"] = st.total_wanted;
  j["downloaded_bytes"] = st.total_wanted_done;
  j["download_rate"] = st.download_payload_rate;
  j["upload_rate"] = st.upload_payload_rate;
  j["num_peers"] = st.num_peers;

  std::string status = "downloading";
  if (job.paused || (st.flags & lt::torrent_flags::paused))
    status = "paused";
  else if (!st.has_metadata)
    status = "metadata";
  else if (st.is_finished || st.progress >= 0.999f)
    status = "completed";
  else if (job.mode == DownloadMode::Stream)
    status = "streaming";
  else if (!job.error.empty())
    status = "error";
  j["status"] = status;

  nlohmann::json files = nlohmann::json::array();
  if (auto ti = job.handle.torrent_file()) {
    for (int i = 0; i < ti->num_files(); ++i) {
      nlohmann::json f;
      f["index"] = i;
      f["name"] = std::string(ti->layout().file_name(lt::file_index_t{i}));
      f["length"] = ti->layout().file_size(lt::file_index_t{i});
      files.push_back(f);
    }
  }
  j["files"] = files;
  if (job.media_file_index >= 0 && job.media_file_index < (int)files.size()) {
    j["file_name"] = files[job.media_file_index]["name"];
    if (auto ti = job.handle.torrent_file()) {
      auto abs = ti->layout().file_path(lt::file_index_t{job.media_file_index},
                                       data_dir_.string());
      j["media_path"] = abs;
    }
  }
  j["abr_height"] = job.abr_height;
  j["boost"] = {
      {"active", job.boost_until_ms > 0},
      {"multiplier", job.boost_mult},
      {"until_ms", job.boost_until_ms},
  };
  j["buffer"] = compute_buffer_locked(job);
  return j;
}

nlohmann::json TorrentManager::compute_buffer_locked(const TorrentJob& job) const {
  nlohmann::json b;
  b["playhead_bytes"] = job.playhead_bytes;
  b["playhead_window_bytes"] = job.profile.playhead_window_bytes;
  b["lookahead_bytes"] = job.profile.lookahead_bytes;
  b["contiguous_ahead_bytes"] = 0;
  b["estimated_ahead_sec"] = nullptr;
  b["underrun"] = false;
  b["warning"] = nullptr;

  if (!job.handle.is_valid() || job.media_file_index < 0) return b;
  auto ti = job.handle.torrent_file();
  if (!ti) return b;

  const auto& fs = ti->layout();
  const auto file_idx = lt::file_index_t{job.media_file_index};
  const std::int64_t file_size = fs.file_size(file_idx);
  const std::int64_t file_off = fs.file_offset(file_idx);
  const int piece_length = ti->piece_length();
  if (piece_length <= 0 || file_size <= 0) return b;

  auto have = job.handle.status().pieces;
  if (static_cast<int>(have.size()) < ti->num_pieces()) return b;
  std::int64_t ahead = 0;
  std::int64_t pos = job.playhead_bytes;
  while (pos < file_size) {
    int pi = static_cast<int>((file_off + pos) / piece_length);
    if (pi < 0 || pi >= ti->num_pieces()) break;
    if (!have.get_bit(lt::piece_index_t{pi})) break;
    std::int64_t piece_start = static_cast<std::int64_t>(pi) * piece_length - file_off;
    std::int64_t piece_end = piece_start + piece_length;
    std::int64_t from = std::max(pos, piece_start);
    std::int64_t to = std::min(file_size, piece_end);
    if (to <= from) break;
    ahead += (to - from);
    pos = to;
  }
  b["contiguous_ahead_bytes"] = ahead;
  b["sequential_bytes"] = ahead;

  auto st = job.handle.status();
  // Playback bitrate, not swarm speed. A fast download must not shrink
  // the time estimate and flash "buffer low".
  const double media_bps = 4.5e6;
  double ahead_sec = (ahead * 8.0) / media_bps;
  b["estimated_ahead_sec"] = ahead_sec;
  b["buffer_ahead_seconds"] = ahead_sec;
  const bool almost_done = st.progress >= 0.999f;
  const bool streaming = job.mode == DownloadMode::Stream;
  const std::int64_t low_bytes = 16LL * 1024 * 1024;
  const std::int64_t underrun_bytes = 6LL * 1024 * 1024;
  bool underrun = streaming && !almost_done && ahead < underrun_bytes;
  b["underrun"] = underrun;
  if (underrun)
    b["warning"] = "buffer underrun — boost ahead or wait for peers";
  else if (streaming && !almost_done && ahead < low_bytes)
    b["warning"] = "buffer low";
  // Note: underrun counter incremented by HTTP layer / buffer poller.
  return b;
}

std::int64_t TorrentManager::sequential_bytes(const std::string& id,
                                              std::int64_t from_byte) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return 0;
  auto buf = compute_buffer_locked(*it->second);
  // compute_buffer uses job.playhead — temporarily compute from from_byte
  auto& job = *it->second;
  auto saved = job.playhead_bytes;
  job.playhead_bytes = std::max<std::int64_t>(0, from_byte);
  buf = compute_buffer_locked(job);
  job.playhead_bytes = saved;
  return buf.value("contiguous_ahead_bytes", 0);
}

nlohmann::json TorrentManager::hls_readiness(const std::string& id,
                                             std::int64_t from_byte,
                                             std::int64_t min_bytes) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end())
    return nlohmann::json{{"error", "not found"}, {"ready", false}};
  auto& job = *it->second;
  nlohmann::json out;
  out["id"] = id;
  out["ready"] = false;
  out["retry"] = true;

  if (!job.handle.is_valid() || job.media_file_index < 0) {
    out["error"] = "media not ready";
    out["reason"] = "no_media_file";
    return out;
  }
  auto ti = job.handle.torrent_file();
  if (!ti) {
    out["error"] = "metadata pending";
    out["reason"] = "no_metadata";
    return out;
  }
  const auto file_idx = lt::file_index_t{job.media_file_index};
  const std::int64_t file_size = ti->layout().file_size(file_idx);
  std::string fname =
      std::string(ti->layout().file_name(file_idx));
  out["file_name"] = fname;
  out["file_size"] = file_size;
  out["pipeable"] = is_pipeable_media_name(fname);
  out["seek_heavy"] = is_seek_heavy_media_name(fname);
  // Progressive Range is preferred for MP4/MOV; HLS for pipeable containers.
  out["preferred_mode"] =
      out["seek_heavy"].get<bool>() ? "progressive" : "hls";
  out["play_hint"] =
      out["seek_heavy"].get<bool>()
          ? "use /media Range (or mpv/VLC); HLS needs near-complete MP4"
          : "HLS remux OK after sequential gate";

  auto saved = job.playhead_bytes;
  job.playhead_bytes = std::max<std::int64_t>(0, from_byte);
  auto buf = compute_buffer_locked(job);
  job.playhead_bytes = saved;
  std::int64_t seq = buf.value("contiguous_ahead_bytes", 0);
  out["sequential_bytes"] = seq;
  out["buffer"] = buf;

  std::int64_t need = min_bytes;
  if (need <= 0) {
    if (out["seek_heavy"].get<bool>())
      need = file_size;  // need near-complete for MP4 HLS remux
    else
      need = 16LL * 1024 * 1024;  // 16 MiB gate for pipeable containers
  }
  if (need > file_size) need = file_size;
  out["required_bytes"] = need;

  auto st = job.handle.status();
  if (out["seek_heavy"].get<bool>() && st.progress < 0.98f && seq < need) {
    out["error"] = "MP4/MOV needs more data before HLS (use /media Range meanwhile)";
    out["reason"] = "seek_heavy_incomplete";
    return out;
  }
  if (seq < need) {
    out["error"] = "buffering torrent — need more sequential bytes";
    out["reason"] = "sequential_gate";
    return out;
  }
  out["ready"] = true;
  out["retry"] = false;
  out["error"] = nullptr;
  return out;
}

bool TorrentManager::wait_media_range(const std::string& id,
                                      std::int64_t start,
                                      std::int64_t end,
                                      int timeout_ms) {
  auto t0 = std::chrono::steady_clock::now();
  auto deadline =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    {
      std::lock_guard lock(mu_);
      auto it = jobs_.find(id);
      if (it == jobs_.end()) {
        note_piece_wait(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - t0)
                .count(),
            false);
        return false;
      }
      auto& job = *it->second;
      if (!job.handle.is_valid() || !job.handle.torrent_file() ||
          job.media_file_index < 0) {
        note_piece_wait(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - t0)
                .count(),
            false);
        return false;
      }
      auto ti = job.handle.torrent_file();
      const auto& fs = ti->layout();
      const auto fidx = lt::file_index_t{job.media_file_index};
      const std::int64_t file_off = fs.file_offset(fidx);
      const std::int64_t file_size = fs.file_size(fidx);
      const int piece_length = ti->piece_length();
      if (piece_length <= 0) {
        note_piece_wait(0, false);
        return false;
      }
      start = std::max<std::int64_t>(0, start);
      end = std::min(file_size - 1, end);
      if (start > end) {
        note_piece_wait(0, true);
        return true;
      }
      const int first = static_cast<int>((file_off + start) / piece_length);
      const int last = static_cast<int>((file_off + end) / piece_length);
      bool ready = true;
      for (int i = first; i <= last; ++i) {
        if (!job.handle.have_piece(lt::piece_index_t{i})) {
          ready = false;
          job.handle.piece_priority(lt::piece_index_t{i}, lt::top_priority);
          job.handle.set_piece_deadline(lt::piece_index_t{i}, 500);
        }
      }
      if (ready) {
        note_piece_wait(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::steady_clock::now() - t0)
                .count(),
            true);
        return true;
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
  }
  note_piece_wait(
      std::chrono::duration_cast<std::chrono::milliseconds>(
          std::chrono::steady_clock::now() - t0)
          .count(),
      false);
  return false;
}

bool TorrentManager::set_media_file(const std::string& id, int file_index) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return false;
  auto& job = *it->second;
  if (!job.handle.is_valid() || !job.handle.torrent_file()) return false;
  auto ti = job.handle.torrent_file();
  if (file_index < 0 || file_index >= ti->num_files()) return false;
  job.media_file_index = file_index;
  job.playhead_bytes = 0;
  // Selective: only download the chosen media file (plus tiny head of others off).
  const int n = ti->num_files();
  std::vector<lt::download_priority_t> prios(static_cast<size_t>(n),
                                             lt::dont_download);
  prios[static_cast<size_t>(file_index)] = lt::top_priority;
  job.handle.prioritize_files(prios);
  apply_stream_policy(job);
  return true;
}

void TorrentManager::note_underrun() { metric_underruns_.fetch_add(1); }
void TorrentManager::note_hls_restart() { metric_hls_restarts_.fetch_add(1); }
void TorrentManager::note_piece_wait(std::int64_t waited_ms, bool ok) {
  metric_piece_waits_.fetch_add(1);
  if (ok) metric_piece_wait_ok_.fetch_add(1);
  if (waited_ms > 0) metric_piece_wait_ms_sum_.fetch_add(waited_ms);
}

int TorrentManager::suggest_abr_height(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end() || !it->second->handle.is_valid()) return 0;
  if (it->second->abr_height > 0) return it->second->abr_height;
  auto st = it->second->handle.status();
  const auto rate = st.download_payload_rate;  // bytes/s
  if (rate < 200 * 1024) return 360;
  if (rate < 800 * 1024) return 720;
  return 1080;
}

nlohmann::json TorrentManager::metrics_snapshot() {
  nlohmann::json j;
  j["underruns"] = metric_underruns_.load();
  j["hls_restarts"] = metric_hls_restarts_.load();
  j["piece_waits"] = metric_piece_waits_.load();
  j["piece_wait_ok"] = metric_piece_wait_ok_.load();
  j["piece_wait_ms_sum"] = metric_piece_wait_ms_sum_.load();
  auto waits = metric_piece_waits_.load();
  j["piece_wait_ms_avg"] =
      waits > 0 ? (metric_piece_wait_ms_sum_.load() / waits) : 0;
  {
    std::lock_guard lock(mu_);
    j["torrents"] = static_cast<int>(jobs_.size());
    int underrunning = 0;
    int peers = 0;
    for (auto& [id, job] : jobs_) {
      (void)id;
      if (!job->handle.is_valid()) continue;
      auto st = job->handle.status();
      peers += st.num_peers;
      auto buf = compute_buffer_locked(*job);
      if (buf.value("underrun", false)) ++underrunning;
    }
    j["peers_total"] = peers;
    j["underrunning_now"] = underrunning;
  }
  return j;
}

nlohmann::json TorrentManager::buffer_health(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return nlohmann::json{{"error", "not found"}};
  auto j = compute_buffer_locked(*it->second);
  j["id"] = id;
  return j;
}

nlohmann::json TorrentManager::boost(const std::string& id,
                                     double duration_sec,
                                     double multiplier) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return nlohmann::json{{"error", "not found"}};
  if (duration_sec < 1) duration_sec = 30;
  if (multiplier < 1.0) multiplier = 2.0;
  if (multiplier > 8.0) multiplier = 8.0;
  auto now = std::chrono::duration_cast<std::chrono::milliseconds>(
                 std::chrono::system_clock::now().time_since_epoch())
                 .count();
  it->second->boost_mult = multiplier;
  it->second->boost_until_ms =
      now + static_cast<std::int64_t>(duration_sec * 1000.0);
  apply_stream_policy(*it->second);
  return nlohmann::json{
      {"id", id},
      {"boost",
       {{"active", true},
        {"multiplier", multiplier},
        {"until_ms", it->second->boost_until_ms},
        {"duration_sec", duration_sec}}},
      {"buffer", compute_buffer_locked(*it->second)},
  };
}

nlohmann::json TorrentManager::set_abr(const std::string& id, int height) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return nlohmann::json{{"error", "not found"}};
  // 0 = auto; otherwise clamp to ladder
  if (height < 0) height = 0;
  if (height > 0 && height < 360) height = 360;
  it->second->abr_height = height;
  nlohmann::json levels = nlohmann::json::array({360, 720, 1080});
  return nlohmann::json{
      {"id", id},
      {"abr_height", height},
      {"abr_auto", height == 0},
      {"levels", levels},
      {"note",
       "Clients should select the matching HLS variant (hls.js level). "
       "Engine stores preference for multi-client sync."},
  };
}

nlohmann::json TorrentManager::invite(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return nlohmann::json{{"error", "not found"}};
  auto& job = *it->second;
  std::string magnet = job.magnet_or_url;
  if (magnet.rfind("magnet:", 0) != 0 && job.handle.is_valid()) {
    auto st = job.handle.status();
    if (st.info_hashes.has_v1()) {
      auto const& h = st.info_hashes.v1;
      static const char* hexd = "0123456789abcdef";
      std::string hex;
      hex.resize(40);
      for (int i = 0; i < 20; ++i) {
        auto b = static_cast<unsigned char>(h[i]);
        hex[static_cast<size_t>(i * 2)] = hexd[b >> 4];
        hex[static_cast<size_t>(i * 2 + 1)] = hexd[b & 0xf];
      }
      magnet = "magnet:?xt=urn:btih:" + hex;
      if (!st.name.empty()) magnet += "&dn=" + st.name;
    }
    magnet = magnet_with_peers(magnet);
  } else if (magnet.rfind("magnet:", 0) == 0) {
    magnet = magnet_with_peers(magnet);
  }
  int port = listen_port();
  nlohmann::json dial = nullptr;
  if (!cfg_.share.public_host.empty() && port > 0)
    dial = cfg_.share.public_host + ":" + std::to_string(port);

  std::string deep = "torrentgrabber://invite?id=" + id;
  if (dial.is_string()) deep += "&peer=" + dial.get<std::string>();
  // qr_text is what 3rd-party / UI should encode into a QR code
  std::string qr = magnet;
  if (dial.is_string()) qr += "\npeer=" + dial.get<std::string>();

  return nlohmann::json{
      {"id", id},
      {"magnet", magnet},
      {"dial_endpoint", dial},
      {"listen_port", port},
      {"public_host",
       cfg_.share.public_host.empty() ? nullptr
                                      : nlohmann::json(cfg_.share.public_host)},
      {"share_profile", cfg_.share.profile},
      {"deep_link", deep},
      {"qr_text", qr},
      {"howto",
       "Share qr_text / deep_link out-of-band. Friend adds magnet with "
       "mode=friend and dial_endpoint via POST /peers if needed."},
  };
}

nlohmann::json TorrentManager::status(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return nlohmann::json{{"error", "not found"}};
  return job_to_json(*it->second);
}

nlohmann::json TorrentManager::list() {
  std::lock_guard lock(mu_);
  nlohmann::json arr = nlohmann::json::array();
  for (auto& [id, job] : jobs_) arr.push_back(job_to_json(*job));
  return arr;
}

bool TorrentManager::pause(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return false;
  if (it->second->handle.is_valid())
    it->second->handle.clear_piece_deadlines();
  it->second->handle.pause();
  it->second->paused = true;
  return true;
}

bool TorrentManager::resume(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return false;
  it->second->handle.resume();
  it->second->paused = false;
  apply_stream_policy(*it->second);
  return true;
}

bool TorrentManager::remove(const std::string& id, bool delete_files) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return false;
  lt::remove_flags_t flags{};
  if (delete_files) flags |= lt::session_handle::delete_files;
  session_->remove_torrent(it->second->handle, flags);
  jobs_.erase(it);
  return true;
}

bool TorrentManager::set_playhead(const std::string& id, std::int64_t offset_bytes) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return false;
  it->second->playhead_bytes = std::max<std::int64_t>(0, offset_bytes);
  apply_stream_policy(*it->second);
  return true;
}

std::shared_ptr<TorrentJob> TorrentManager::get(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return nullptr;
  return it->second;
}

nlohmann::json TorrentManager::session_stats() {
  std::lock_guard lock(mu_);
  nlohmann::json j;
  j["torrents"] = jobs_.size();
  j["data_dir"] = data_dir_.string();
  int port = listen_port();
  j["listen_port"] = port;
  j["public_host"] = cfg_.share.public_host.empty()
                         ? nullptr
                         : nlohmann::json(cfg_.share.public_host);
  if (!cfg_.share.public_host.empty() && port > 0) {
    j["dial_endpoint"] =
        cfg_.share.public_host + ":" + std::to_string(port);
  } else {
    j["dial_endpoint"] = nullptr;
  }
  j["internet_share"] = {
      {"uses", "BitTorrent listen port (share.listen_port) + public_host / x.pe"},
      {"not", "HTTP API bind (keep on 127.0.0.1)"},
      {"steps",
       nlohmann::json::array(
           {"Set share.listen_port (e.g. 6881) in config.yaml",
            "Set share.public_host to Tailscale/LAN/public IP, or "
            "auto_detect_public_host: true",
            "Forward UDP+TCP share.listen_port on the router (or rely on "
            "UPnP/NAT-PMP / Tailscale)",
            "POST /v1/share — magnet includes &x.pe=host:port when public_host "
            "is set",
            "Friend adds magnet with mode=friend (or POST /peers with "
            "dial_endpoint)"})}};
  j["share_profile"] = cfg_.share.profile;
  if (!cfg_.loaded_from.empty()) j["config"] = cfg_.loaded_from.string();
  return j;
}

std::optional<std::filesystem::path> TorrentManager::media_path(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = jobs_.find(id);
  if (it == jobs_.end()) return std::nullopt;
  auto& job = *it->second;
  if (!job.handle.is_valid()) return std::nullopt;
  auto ti = job.handle.torrent_file();
  if (!ti || job.media_file_index < 0) return std::nullopt;
  auto st = job.handle.status();
  auto save = st.save_path.empty() ? data_dir_.string() : st.save_path;
  auto abs = ti->layout().file_path(lt::file_index_t{job.media_file_index}, save);
  return std::filesystem::path(abs);
}

}  // namespace te
