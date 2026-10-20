#include "http_server.hpp"
#include "util.hpp"

#include <chrono>
#include <fstream>
#include <iostream>
#include <optional>
#include <sstream>

#define CPPHTTPLIB_NO_EXCEPTIONS
#include "httplib.h"
#include "updater.hpp"
#include "version.hpp"

namespace te {

struct ServerHolder {
  httplib::Server svr;
};

HttpServer::HttpServer(EngineConfig cfg,
                       std::shared_ptr<TorrentManager> torrents,
                       std::shared_ptr<FfmpegBridge> ffmpeg,
                       std::shared_ptr<PartyManager> parties)
    : cfg_(std::move(cfg)),
      torrents_(std::move(torrents)),
      ffmpeg_(std::move(ffmpeg)),
      parties_(parties ? std::move(parties) : std::make_shared<PartyManager>()) {
  server_ = new ServerHolder();
  setup_routes();
}

HttpServer::~HttpServer() {
  stop();
  delete static_cast<ServerHolder*>(server_);
  server_ = nullptr;
}

void HttpServer::stop() {
  if (!server_) return;
  static_cast<ServerHolder*>(server_)->svr.stop();
}

void HttpServer::setup_routes() {
  auto& svr = static_cast<ServerHolder*>(server_)->svr;
  auto torrents = torrents_;
  auto ffmpeg = ffmpeg_;
  auto work_root = cfg_.data_dir / "hls";

  svr.Get("/v1/health", [this, torrents](const httplib::Request&, httplib::Response& res) {
    nlohmann::json j = version_json();
    j["ok"] = true;
    j["port"] = port_;
    j["session"] = torrents->session_stats();
    j["metrics"] = {
        {"hls_starts", metrics_hls_starts_},
        {"hls_gate_rejects", metrics_hls_gate_},
        {"hls_errors", metrics_hls_errors_},
    };
    res.set_content(j.dump(), "application/json");
  });

  svr.Get("/v1/version", [](const httplib::Request&, httplib::Response& res) {
    res.set_content(version_json().dump(), "application/json");
  });

  svr.Get("/v1/update/check", [this](const httplib::Request&, httplib::Response& res) {
    auto url = resolve_manifest_url(cfg_.update_manifest_url);
    auto check = check_for_update(url);
    nlohmann::json j = version_json();
    j["manifest_url"] = url;
    j["ok"] = check.ok;
    j["update_available"] = check.update_available;
    j["latest_version"] = check.latest_version;
    j["notes"] = check.notes;
    j["asset_url"] = check.asset_url;
    if (!check.sdk.is_null()) j["sdk"] = check.sdk;
    if (!check.error.empty()) j["error"] = check.error;
    res.status = check.ok ? 200 : 503;
    res.set_content(j.dump(), "application/json");
  });

  svr.Get("/v1/metrics", [this, torrents](const httplib::Request&, httplib::Response& res) {
    auto st = torrents->session_stats();
    auto m = torrents->metrics_snapshot();
    std::ostringstream o;
    o << "# TYPE torrent_engine_hls_starts counter\n";
    o << "torrent_engine_hls_starts " << metrics_hls_starts_ << "\n";
    o << "# TYPE torrent_engine_hls_gate_rejects counter\n";
    o << "torrent_engine_hls_gate_rejects " << metrics_hls_gate_ << "\n";
    o << "# TYPE torrent_engine_hls_errors counter\n";
    o << "torrent_engine_hls_errors " << metrics_hls_errors_ << "\n";
    o << "# TYPE torrent_engine_hls_restarts counter\n";
    o << "torrent_engine_hls_restarts " << m.value("hls_restarts", 0) << "\n";
    o << "# TYPE torrent_engine_underruns counter\n";
    o << "torrent_engine_underruns " << m.value("underruns", 0) << "\n";
    o << "# TYPE torrent_engine_piece_waits counter\n";
    o << "torrent_engine_piece_waits " << m.value("piece_waits", 0) << "\n";
    o << "# TYPE torrent_engine_piece_wait_ok counter\n";
    o << "torrent_engine_piece_wait_ok " << m.value("piece_wait_ok", 0) << "\n";
    o << "# TYPE torrent_engine_piece_wait_ms_sum counter\n";
    o << "torrent_engine_piece_wait_ms_sum " << m.value("piece_wait_ms_sum", 0)
      << "\n";
    o << "# TYPE torrent_engine_peers gauge\n";
    o << "torrent_engine_peers " << m.value("peers_total", 0) << "\n";
    o << "# TYPE torrent_engine_underrunning gauge\n";
    o << "torrent_engine_underrunning " << m.value("underrunning_now", 0) << "\n";
    if (st.contains("torrents"))
      o << "torrent_engine_torrents " << st.value("torrents", 0) << "\n";
    res.set_content(o.str(), "text/plain; version=0.0.4");
  });

  svr.Get("/v1/metrics.json", [this, torrents](const httplib::Request&, httplib::Response& res) {
    auto m = torrents->metrics_snapshot();
    m["hls_starts"] = metrics_hls_starts_;
    m["hls_gate_rejects"] = metrics_hls_gate_;
    m["hls_errors"] = metrics_hls_errors_;
    m["session"] = torrents->session_stats();
    res.set_content(m.dump(), "application/json");
  });

  svr.Get("/v1/session", [torrents](const httplib::Request&, httplib::Response& res) {
    res.set_content(torrents->session_stats().dump(), "application/json");
  });

  svr.Post("/v1/share", [torrents](const httplib::Request& req, httplib::Response& res) {
    nlohmann::json body;
    try {
      body = nlohmann::json::parse(req.body.empty() ? "{}" : req.body);
    } catch (...) {
      res.status = 400;
      res.set_content(R"({"error":"invalid json"})", "application/json");
      return;
    }
    std::string path = body.value("path", "");
    if (path.empty()) {
      res.status = 400;
      res.set_content(R"({"error":"path required"})", "application/json");
      return;
    }
    bool priv = body.value("private", torrents->config().share.private_by_default);
    std::optional<std::string> name;
    if (body.contains("name") && body["name"].is_string())
      name = body["name"].get<std::string>();
    std::optional<std::string> comment;
    if (body.contains("comment") && body["comment"].is_string())
      comment = body["comment"].get<std::string>();
    std::optional<std::string> id;
    if (body.contains("id") && body["id"].is_string())
      id = body["id"].get<std::string>();

    auto out = torrents->share_path(path, priv, name, comment, id);
    if (out.contains("error") && !out["error"].is_null()) {
      res.status = 502;
      res.set_content(out.dump(), "application/json");
      return;
    }
    res.set_content(out.dump(), "application/json");
  });

  svr.Post("/v1/torrents", [torrents](const httplib::Request& req,
                                      httplib::Response& res) {
    nlohmann::json body;
    try {
      body = nlohmann::json::parse(req.body.empty() ? "{}" : req.body);
    } catch (...) {
      res.status = 400;
      res.set_content(R"({"error":"invalid json"})", "application/json");
      return;
    }
    std::string magnet = body.value("magnet", body.value("magnet_or_url", ""));
    if (magnet.empty()) {
      res.status = 400;
      res.set_content(R"({"error":"magnet, local .torrent path, or http(s) URL required"})",
                      "application/json");
      return;
    }
    std::string mode_s = body.value("mode", body.value("preset", "general"));
    auto mode = parse_mode(mode_s);
    std::vector<int> selective;
    if (body.contains("file_indexes") && body["file_indexes"].is_array()) {
      for (auto& x : body["file_indexes"]) selective.push_back(x.get<int>());
    }
    std::vector<std::pair<std::string, int>> peers;
    if (body.contains("peers") && body["peers"].is_array()) {
      for (auto& p : body["peers"]) {
        if (p.is_string()) {
          auto s = p.get<std::string>();
          auto colon = s.rfind(':');
          if (colon != std::string::npos) {
            peers.emplace_back(s.substr(0, colon), std::stoi(s.substr(colon + 1)));
          }
        } else if (p.is_object()) {
          peers.emplace_back(p.value("ip", ""), p.value("port", 0));
        }
      }
    }
    std::optional<std::string> id;
    if (body.contains("id") && body["id"].is_string())
      id = body["id"].get<std::string>();

    auto out = torrents->add(magnet, mode, selective, id, peers);
    if (out.contains("error") && !out["error"].is_null()) {
      res.status = 502;
      res.set_content(out.dump(), "application/json");
      return;
    }
    res.status = 200;
    res.set_content(out.dump(), "application/json");
  });

  svr.Get("/v1/torrents", [torrents](const httplib::Request&, httplib::Response& res) {
    res.set_content(torrents->list().dump(), "application/json");
  });

  svr.Get("/v1/torrents/:id",
          [torrents](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto j = torrents->status(id);
            if (j.contains("error") && !j["error"].is_null() &&
                j["error"] == "not found")
              res.status = 404;
            res.set_content(j.dump(), "application/json");
          });

  svr.Post("/v1/torrents/:id/pause",
           [torrents](const httplib::Request& req, httplib::Response& res) {
             auto id = req.path_params.at("id");
             if (!torrents->pause(id)) {
               res.status = 404;
               res.set_content(R"({"error":"not found"})", "application/json");
               return;
             }
             res.set_content(torrents->status(id).dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/resume",
           [torrents](const httplib::Request& req, httplib::Response& res) {
             auto id = req.path_params.at("id");
             if (!torrents->resume(id)) {
               res.status = 404;
               res.set_content(R"({"error":"not found"})", "application/json");
               return;
             }
             res.set_content(torrents->status(id).dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/peers",
           [torrents](const httplib::Request& req, httplib::Response& res) {
             auto id = req.path_params.at("id");
             nlohmann::json body;
             try {
               body = nlohmann::json::parse(req.body.empty() ? "{}" : req.body);
             } catch (...) {
               res.status = 400;
               res.set_content(R"({"error":"invalid json"})", "application/json");
               return;
             }
             std::string ip = body.value("ip", "");
             int port = body.value("port", 0);
             if (ip.empty() || port <= 0) {
               res.status = 400;
               res.set_content(R"({"error":"ip and port required"})",
                               "application/json");
               return;
             }
             if (!torrents->add_peer(id, ip, port)) {
               res.status = 404;
               res.set_content(R"({"error":"not found or bad peer"})",
                               "application/json");
               return;
             }
             nlohmann::json j{{"ok", true}, {"id", id}, {"ip", ip}, {"port", port}};
             res.set_content(j.dump(), "application/json");
           });

  svr.Get("/v1/torrents/:id/torrent",
          [torrents](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto path = torrents->torrent_file_path(id);
            if (!path) {
              res.status = 404;
              res.set_content(R"({"error":"no .torrent for this id"})",
                              "application/json");
              return;
            }
            std::ifstream in(*path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(in)),
                             std::istreambuf_iterator<char>());
            res.set_header("Content-Disposition",
                           "attachment; filename=\"" + id + ".torrent\"");
            res.set_content(std::move(body), "application/x-bittorrent");
          });

  svr.Delete("/v1/torrents/:id",
             [torrents](const httplib::Request& req, httplib::Response& res) {
               auto id = req.path_params.at("id");
               bool del = req.has_param("delete_files") &&
                          req.get_param_value("delete_files") == "true";
               bool ok = torrents->remove(id, del);
               nlohmann::json j{{"deleted", ok}, {"id", id}};
               res.set_content(j.dump(), "application/json");
             });

  svr.Post("/v1/torrents/:id/playhead",
           [torrents](const httplib::Request& req, httplib::Response& res) {
             auto id = req.path_params.at("id");
             nlohmann::json body;
             try {
               body = nlohmann::json::parse(req.body.empty() ? "{}" : req.body);
             } catch (...) {
               res.status = 400;
               res.set_content(R"({"error":"invalid json"})", "application/json");
               return;
             }
             std::int64_t offset = body.value("offset_bytes", (std::int64_t)0);
             if (!torrents->set_playhead(id, offset)) {
               res.status = 404;
               res.set_content(R"({"error":"not found"})", "application/json");
               return;
             }
             res.set_content(torrents->status(id).dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/media-file",
           [torrents](const httplib::Request& req, httplib::Response& res) {
             auto id = req.path_params.at("id");
             nlohmann::json body;
             try {
               body = nlohmann::json::parse(req.body.empty() ? "{}" : req.body);
             } catch (...) {
               res.status = 400;
               res.set_content(R"({"error":"invalid json"})", "application/json");
               return;
             }
             int idx = body.value("index", -1);
             if (idx < 0 && req.has_param("index"))
               idx = std::stoi(req.get_param_value("index"));
             if (!torrents->set_media_file(id, idx)) {
               res.status = 400;
               res.set_content(R"({"error":"invalid file index"})", "application/json");
               return;
             }
             res.set_content(torrents->status(id).dump(), "application/json");
           });

  svr.Get("/v1/torrents/:id/playlist.m3u",
          [this, torrents](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto st = torrents->status(id);
            if (st.contains("error") && !st["error"].is_null() &&
                st.value("status", "") == "error" && !st.contains("id")) {
              res.status = 404;
              res.set_content(R"({"error":"not found"})", "application/json");
              return;
            }
            if (!st.contains("id") && st.contains("error")) {
              res.status = 404;
              res.set_content(st.dump(), "application/json");
              return;
            }
            std::string title = st.value("name", id);
            std::string host = cfg_.bind;
            if (host == "0.0.0.0") host = "127.0.0.1";
            std::ostringstream body;
            body << "#EXTM3U\n#EXTINF:-1," << title << "\n";
            body << "http://" << host << ":" << port_ << "/v1/torrents/" << id
                 << "/media\n";
            res.set_content(body.str(), "audio/x-mpegurl");
          });

  svr.Get("/v1/playlist.m3u",
          [this, torrents](const httplib::Request&, httplib::Response& res) {
            auto list = torrents->list();
            std::string host = cfg_.bind;
            if (host == "0.0.0.0") host = "127.0.0.1";
            std::ostringstream body;
            body << "#EXTM3U\n";
            if (list.is_array()) {
              for (auto& t : list) {
                if (!t.contains("id")) continue;
                std::string id = t["id"].get<std::string>();
                std::string title = t.value("name", id);
                body << "#EXTINF:-1," << title << "\n";
                body << "http://" << host << ":" << port_ << "/v1/torrents/" << id
                     << "/media\n";
              }
            }
            res.set_content(body.str(), "audio/x-mpegurl");
          });

  svr.Get("/v1/torrents/:id/buffer",
          [torrents](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto j = torrents->buffer_health(id);
            if (j.contains("error")) res.status = 404;
            if (j.value("underrun", false)) {
              torrents->note_underrun();
              torrents->boost(id, 25.0, 2.5);
            }
            res.set_content(j.dump(), "application/json");
          });

  svr.Post("/v1/torrents/:id/boost",
           [torrents](const httplib::Request& req, httplib::Response& res) {
             auto id = req.path_params.at("id");
             double dur = 45, mult = 3.0;
             if (req.has_param("duration"))
               dur = std::stod(req.get_param_value("duration"));
             if (req.has_param("multiplier"))
               mult = std::stod(req.get_param_value("multiplier"));
             if (!req.body.empty()) {
               try {
                 auto body = nlohmann::json::parse(req.body);
                 if (body.contains("duration")) dur = body["duration"].get<double>();
                 if (body.contains("multiplier"))
                   mult = body["multiplier"].get<double>();
               } catch (...) {
               }
             }
             auto j = torrents->boost(id, dur, mult);
             if (j.contains("error")) res.status = 404;
             res.set_content(j.dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/abr",
           [torrents](const httplib::Request& req, httplib::Response& res) {
             auto id = req.path_params.at("id");
             int height = 0;
             if (req.has_param("height"))
               height = std::stoi(req.get_param_value("height"));
             if (!req.body.empty()) {
               try {
                 auto body = nlohmann::json::parse(req.body);
                 if (body.contains("height")) height = body["height"].get<int>();
               } catch (...) {
               }
             }
             auto j = torrents->set_abr(id, height);
             if (j.contains("error")) res.status = 404;
             res.set_content(j.dump(), "application/json");
           });

  svr.Get("/v1/torrents/:id/invite",
          [torrents](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto j = torrents->invite(id);
            if (j.contains("error")) res.status = 404;
            res.set_content(j.dump(), "application/json");
          });

  // Watch party (shared clock; BT remains the data plane)
  auto parties = parties_;
  svr.Post("/v1/party", [parties](const httplib::Request& req, httplib::Response& res) {
    nlohmann::json body = nlohmann::json::object();
    try {
      if (!req.body.empty()) body = nlohmann::json::parse(req.body);
    } catch (...) {
      res.status = 400;
      res.set_content(R"({"error":"invalid json"})", "application/json");
      return;
    }
    std::string magnet = body.value("magnet", "");
    std::optional<std::string> host_peer, torrent_id, room_id;
    if (body.contains("host_peer") && body["host_peer"].is_string())
      host_peer = body["host_peer"].get<std::string>();
    if (body.contains("torrent_id") && body["torrent_id"].is_string())
      torrent_id = body["torrent_id"].get<std::string>();
    if (body.contains("id") && body["id"].is_string())
      room_id = body["id"].get<std::string>();
    auto j = parties->create(magnet, host_peer, torrent_id, room_id);
    if (j.contains("error")) res.status = 400;
    res.set_content(j.dump(), "application/json");
  });

  svr.Get("/v1/party", [parties](const httplib::Request&, httplib::Response& res) {
    res.set_content(parties->list().dump(), "application/json");
  });

  svr.Get("/v1/party/:id", [parties](const httplib::Request& req, httplib::Response& res) {
    auto j = parties->get(req.path_params.at("id"));
    if (j.contains("error")) res.status = 404;
    res.set_content(j.dump(), "application/json");
  });

  svr.Post("/v1/party/:id/sync",
           [parties](const httplib::Request& req, httplib::Response& res) {
             nlohmann::json body = nlohmann::json::object();
             try {
               if (!req.body.empty()) body = nlohmann::json::parse(req.body);
             } catch (...) {
               res.status = 400;
               res.set_content(R"({"error":"invalid json"})", "application/json");
               return;
             }
             double pos = body.value("position_sec", 0.0);
             bool paused = body.value("paused", true);
             std::string who = body.value("who", "peer");
             auto j = parties->sync(req.path_params.at("id"), pos, paused, who);
             if (j.contains("error")) res.status = 404;
             res.set_content(j.dump(), "application/json");
           });

  svr.Delete("/v1/party/:id", [parties](const httplib::Request& req, httplib::Response& res) {
    bool ok = parties->remove(req.path_params.at("id"));
    res.set_content(nlohmann::json{{"deleted", ok}}.dump(), "application/json");
  });

  // One-tap cast session — returns controllable URLs + auth token for 3rd parties
  svr.Post(
      "/v1/torrents/:id/cast/session",
      [this, torrents, ffmpeg, work_root](const httplib::Request& req,
                                         httplib::Response& res) {
        auto id = req.path_params.at("id");
        std::string mode = "export";
        std::string profile = "chromecast";
        if (req.has_param("mode")) mode = req.get_param_value("mode");
        if (req.has_param("profile")) profile = req.get_param_value("profile");
        if (!req.body.empty()) {
          try {
            auto body = nlohmann::json::parse(req.body);
            if (body.contains("mode")) mode = body["mode"].get<std::string>();
            if (body.contains("profile"))
              profile = body["profile"].get<std::string>();
          } catch (...) {
          }
        }
        if (!ffmpeg || !ffmpeg->available()) {
          res.status = 503;
          res.set_content(R"({"error":"ffmpeg missing"})", "application/json");
          return;
        }
        auto path = torrents->media_path(id);
        if (!path || !std::filesystem::exists(*path)) {
          res.status = 503;
          res.set_content(R"({"error":"media not ready"})", "application/json");
          return;
        }

        nlohmann::json out;
        std::string file;
        if (mode == "hls") {
          te::HlsRequest hr;
          hr.abr = false;
          if (port_ > 0) {
            hr.input_url = "http://" + cfg_.bind + ":" + std::to_string(port_) +
                           "/v1/torrents/" + id + "/media";
          }
          torrents->set_playhead(id, 0);
          auto hls = ffmpeg->ensure_hls(id, *path, hr, work_root);
          if (hls.contains("error")) {
            res.status = hls.value("retry", false) ? 503 : 502;
            res.set_content(hls.dump(), "application/json");
            return;
          }
          file = "index.m3u8";
          out["playlist_url"] = "/v1/torrents/" + id + "/hls/index.m3u8";
        } else {
          auto cast = ffmpeg->cast_export(id, *path, work_root, profile);
          if (cast.contains("error")) {
            res.status = 502;
            res.set_content(cast.dump(), "application/json");
            return;
          }
          file = cast.value("file", "");
          out["export_url"] = "/v1/torrents/" + id + "/exports/" + file;
          out["profile"] = profile;
        }

        CastSession sess;
        sess.token = make_id() + make_id();
        sess.download_id = id;
        sess.mode = mode;
        sess.file = file;
        sess.expires_ms =
            std::chrono::duration_cast<std::chrono::milliseconds>(
                std::chrono::system_clock::now().time_since_epoch())
                .count() +
            3600LL * 1000;
        {
          std::lock_guard lock(cast_mu_);
          cast_sessions_[sess.token] = sess;
        }
        out["token"] = sess.token;
        out["expires_ms"] = sess.expires_ms;
        out["mode"] = mode;
        out["download_id"] = id;
        out["cast_url"] =
            (mode == "hls" ? out["playlist_url"] : out["export_url"])
                .get<std::string>() +
            "?token=" + sess.token;
        out["howto"] =
            "Point Chromecast/AirPlay sender or LAN player at cast_url. "
            "Pass token as query. 3rd-party apps fully control via this API.";
        res.set_content(out.dump(), "application/json");
      });

  svr.Get("/v1/torrents/:id/media",
          [torrents](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            std::string range;
            if (req.has_header("Range")) range = req.get_header_value("Range");
            serve_media_range(*torrents, id, range, nullptr, &res);
          });

  svr.Get("/v1/torrents/:id/tracks",
          [torrents, ffmpeg](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            try {
              if (!ffmpeg || !ffmpeg->available()) {
                res.status = 503;
                res.set_content(
                    R"({"error":"ffmpeg/ffprobe missing — set --ffmpeg-dir"})",
                    "application/json");
                return;
              }
              auto path = torrents->media_path(id);
              if (!path || !std::filesystem::exists(*path)) {
                res.status = 503;
                res.set_content(R"({"error":"media not on disk yet","retry":true})",
                                "application/json");
                return;
              }
              auto j = ffmpeg->probe(*path);
              auto st = torrents->status(id);
              if (st.contains("file_name")) j["file_name"] = st["file_name"];
              if (j.contains("error") && !j["error"].is_null()) res.status = 502;
              res.set_content(j.dump(), "application/json");
            } catch (const std::exception& ex) {
              res.status = 502;
              nlohmann::json err{{"error", ex.what()}};
              res.set_content(err.dump(), "application/json");
            } catch (...) {
              res.status = 502;
              res.set_content(R"({"error":"tracks probe crashed"})",
                              "application/json");
            }
          });

  svr.Post("/v1/torrents/:id/hls",
           [this, torrents, ffmpeg, work_root](const httplib::Request& req,
                                               httplib::Response& res) {
             auto id = req.path_params.at("id");
             if (!ffmpeg || !ffmpeg->available()) {
               res.status = 503;
               res.set_content(R"({"error":"ffmpeg missing"})", "application/json");
               return;
             }
             auto path = torrents->media_path(id);
             if (!path || !std::filesystem::exists(*path)) {
               res.status = 503;
               res.set_content(R"({"error":"media not ready"})", "application/json");
               return;
             }

             nlohmann::json body = nlohmann::json::object();
             if (!req.body.empty()) {
               try {
                 body = nlohmann::json::parse(req.body);
               } catch (...) {
                 res.status = 400;
                 res.set_content(R"({"error":"invalid json"})", "application/json");
                 return;
               }
             }

             te::HlsRequest hr;
             // Read via /media so Range requests wait on missing torrent pieces
             // instead of ffmpeg hitting sparse zeros in the on-disk file.
             if (port_ > 0) {
               hr.input_url = "http://" + cfg_.bind + ":" + std::to_string(port_) +
                              "/v1/torrents/" + id + "/media";
             }
             torrents->set_playhead(id, 0);
             auto qint = [&](const char* name) -> std::optional<int> {
               if (req.has_param(name) && !req.get_param_value(name).empty())
                 return std::stoi(req.get_param_value(name));
               return std::nullopt;
             };
             if (auto a = qint("audio")) hr.audio_index = *a;
             else if (body.contains("audio") && !body["audio"].is_null())
               hr.audio_index = body["audio"].get<int>();

             if (req.has_param("start_seconds"))
               hr.start_seconds = std::stod(req.get_param_value("start_seconds"));
             else if (body.contains("start_seconds") && body["start_seconds"].is_number())
               hr.start_seconds = body["start_seconds"].get<double>();

             if (req.has_param("audio_only"))
               hr.audio_only = req.get_param_value("audio_only") == "true" ||
                               req.get_param_value("audio_only") == "1";
             else if (body.contains("audio_only"))
               hr.audio_only = body["audio_only"].get<bool>();

             if (auto b = qint("burn_subtitle")) hr.burn_subtitle = *b;
             else if (body.contains("burn_subtitle") && !body["burn_subtitle"].is_null())
               hr.burn_subtitle = body["burn_subtitle"].get<int>();

             auto qstr = [&](const char* name) -> std::optional<std::string> {
               if (req.has_param(name) && !req.get_param_value(name).empty())
                 return req.get_param_value(name);
               return std::nullopt;
             };
             if (auto v = qstr("audio_bitrate")) hr.audio_bitrate = *v;
             else if (body.contains("audio_bitrate") && body["audio_bitrate"].is_string())
               hr.audio_bitrate = body["audio_bitrate"].get<std::string>();
             if (auto v = qstr("audio_channels")) hr.audio_channels = *v;
             else if (body.contains("audio_channels") && body["audio_channels"].is_string())
               hr.audio_channels = body["audio_channels"].get<std::string>();
             if (auto v = qstr("deinterlace")) hr.deinterlace = *v;
             else if (body.contains("deinterlace") && body["deinterlace"].is_string())
               hr.deinterlace = body["deinterlace"].get<std::string>();
             if (auto v = qstr("tonemap")) hr.tonemap = *v;
             else if (body.contains("tonemap") && body["tonemap"].is_string())
               hr.tonemap = body["tonemap"].get<std::string>();
             if (auto v = qstr("lut_cube")) hr.lut_cube = *v;
             else if (body.contains("lut_cube") && body["lut_cube"].is_string())
               hr.lut_cube = body["lut_cube"].get<std::string>();
             if (req.has_param("loudnorm"))
               hr.loudnorm = req.get_param_value("loudnorm") == "true" ||
                             req.get_param_value("loudnorm") == "1";
             else if (body.contains("loudnorm"))
               hr.loudnorm = body["loudnorm"].get<bool>();
             if (req.has_param("abr"))
               hr.abr = req.get_param_value("abr") == "true" ||
                        req.get_param_value("abr") == "1";
             else if (body.contains("abr"))
               hr.abr = body["abr"].get<bool>();

             // Seek playhead before readiness so sequential_bytes is meaningful.
             std::int64_t from_byte = 0;
             if (hr.start_seconds && *hr.start_seconds > 0) {
               auto st = torrents->status(id);
               std::int64_t total = 0;
               if (st.contains("total_bytes") && st["total_bytes"].is_number())
                 total = st["total_bytes"].get<std::int64_t>();
               double dur = 0;
               // rough: assume ~bitrate from file size / unknown duration → byte offset later
               if (total > 0 && *hr.start_seconds > 0) {
                 // Without duration, use 0 and let ffmpeg -ss handle; gate from start.
                 from_byte = 0;
               }
             }
             if (auto a = torrents->status(id); a.contains("abr_height")) {
               int h = a.value("abr_height", 0);
               if (h <= 0) h = torrents->suggest_abr_height(id);
               hr.max_height = h;
             } else {
               hr.max_height = torrents->suggest_abr_height(id);
             }

             auto ready = torrents->hls_readiness(id, from_byte);
             if (!ready.value("ready", false)) {
               ++metrics_hls_gate_;
               res.status = 503;
               res.set_content(ready.dump(), "application/json");
               return;
             }
             // MP4/MOV: refuse early HLS even harder — preferred_mode progressive
             if (ready.value("seek_heavy", false) &&
                 !ready.value("ready", false)) {
               // already gated above
             }
             if (ready.value("pipeable", false)) hr.prefer_pipe = true;

             // Pre-buffer boost before seek remux
             if (hr.start_seconds && *hr.start_seconds > 0.5) {
               torrents->boost(id, 20.0, 3.0);
               torrents->note_hls_restart();
             }

             ++metrics_hls_starts_;
             auto j = ffmpeg->ensure_hls(id, *path, hr, work_root);
             if (j.contains("error") && !j["error"].is_null()) {
               ++metrics_hls_errors_;
               res.status = j.value("retry", false) ? 503 : 502;
               res.set_content(j.dump(), "application/json");
               return;
             }
             j["preferred_mode"] = ready.value("preferred_mode", "hls");
             j["suggested_abr_height"] = hr.max_height;

             // Sync torrent playhead when seeking HLS
             if (hr.start_seconds && *hr.start_seconds > 0) {
               auto st = torrents->status(id);
               double dur = 0;
               if (j.contains("duration") && j["duration"].is_number())
                 dur = j["duration"].get<double>();
               // probe duration not in ensure response — use status size + start ratio
               std::int64_t total = 0;
               if (st.contains("total_bytes") && st["total_bytes"].is_number())
                 total = st["total_bytes"].get<std::int64_t>();
               else if (st.contains("size") && st["size"].is_number())
                 total = st["size"].get<std::int64_t>();
               if (total > 0) {
                 // Prefer duration from tracks if available via a quick field
                 auto tracks = ffmpeg->probe(*path);
                 if (tracks.contains("duration") && tracks["duration"].is_number())
                   dur = tracks["duration"].get<double>();
                 if (dur > 0) {
                   auto off = static_cast<std::int64_t>((*hr.start_seconds / dur) *
                                                        static_cast<double>(total));
                   torrents->set_playhead(id, off);
                 }
               }
             }

             res.set_content(j.dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/poster",
           [torrents, ffmpeg, work_root](const httplib::Request& req,
                                         httplib::Response& res) {
             auto id = req.path_params.at("id");
             if (!ffmpeg || !ffmpeg->available()) {
               res.status = 503;
               res.set_content(R"({"error":"ffmpeg missing"})", "application/json");
               return;
             }
             auto path = torrents->media_path(id);
             if (!path || !std::filesystem::exists(*path)) {
               res.status = 503;
               res.set_content(R"({"error":"media not ready"})", "application/json");
               return;
             }
             auto j = ffmpeg->ensure_poster(id, *path, work_root);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Get("/v1/torrents/:id/poster.jpg",
          [ffmpeg, work_root](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto path = ffmpeg->asset_file(id, "poster", "poster.jpg", work_root);
            if (!path) {
              res.status = 404;
              res.set_content(R"({"error":"poster not ready — POST /poster first"})",
                              "application/json");
              return;
            }
            std::ifstream in(*path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(in)),
                             std::istreambuf_iterator<char>());
            res.set_header("Cache-Control", "no-store");
            res.set_content(std::move(body), "image/jpeg");
          });

  svr.Post("/v1/torrents/:id/thumbs",
           [torrents, ffmpeg, work_root](const httplib::Request& req,
                                         httplib::Response& res) {
             auto id = req.path_params.at("id");
             if (!ffmpeg || !ffmpeg->available()) {
               res.status = 503;
               res.set_content(R"({"error":"ffmpeg missing"})", "application/json");
               return;
             }
             auto path = torrents->media_path(id);
             if (!path || !std::filesystem::exists(*path)) {
               res.status = 503;
               res.set_content(R"({"error":"media not ready"})", "application/json");
               return;
             }
             int interval = 10;
             if (req.has_param("interval"))
               interval = std::stoi(req.get_param_value("interval"));
             if (!req.body.empty()) {
               try {
                 auto body = nlohmann::json::parse(req.body);
                 if (body.contains("interval")) interval = body["interval"].get<int>();
               } catch (...) {
               }
             }
             auto j = ffmpeg->ensure_thumbs(id, *path, work_root, interval);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Get("/v1/torrents/:id/thumbs/:file",
          [ffmpeg, work_root](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto file = url_decode(req.path_params.at("file"));
            auto path = ffmpeg->asset_file(id, "thumbs", file, work_root);
            if (!path) {
              res.status = 404;
              res.set_content(R"({"error":"not ready"})", "application/json");
              return;
            }
            std::ifstream in(*path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(in)),
                             std::istreambuf_iterator<char>());
            res.set_header("Cache-Control", "no-store");
            res.set_content(std::move(body), mime_for_path(file));
          });

  auto require_media =
      [torrents, ffmpeg](const std::string& id, httplib::Response& res)
      -> std::optional<std::filesystem::path> {
    if (!ffmpeg || !ffmpeg->available()) {
      res.status = 503;
      res.set_content(R"({"error":"ffmpeg missing"})", "application/json");
      return std::nullopt;
    }
    auto path = torrents->media_path(id);
    if (!path || !std::filesystem::exists(*path)) {
      res.status = 503;
      res.set_content(R"({"error":"media not ready"})", "application/json");
      return std::nullopt;
    }
    return path;
  };

  svr.Post("/v1/torrents/:id/clip",
           [require_media, ffmpeg, work_root](const httplib::Request& req,
                                              httplib::Response& res) {
             auto id = req.path_params.at("id");
             auto path = require_media(id, res);
             if (!path) return;
             nlohmann::json body = nlohmann::json::object();
             try {
               if (!req.body.empty()) body = nlohmann::json::parse(req.body);
             } catch (...) {
               res.status = 400;
               res.set_content(R"({"error":"invalid json"})", "application/json");
               return;
             }
             double start = body.value("start", 0.0);
             double end = body.value("end", 0.0);
             if (req.has_param("start")) start = std::stod(req.get_param_value("start"));
             if (req.has_param("end")) end = std::stod(req.get_param_value("end"));
             std::string container = body.value("container", "mp4");
             if (req.has_param("container"))
               container = req.get_param_value("container");
             auto j = ffmpeg->export_clip(id, *path, work_root, start, end, container);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/remux",
           [require_media, ffmpeg, work_root](const httplib::Request& req,
                                              httplib::Response& res) {
             auto id = req.path_params.at("id");
             auto path = require_media(id, res);
             if (!path) return;
             nlohmann::json body = nlohmann::json::object();
             try {
               if (!req.body.empty()) body = nlohmann::json::parse(req.body);
             } catch (...) {
               res.status = 400;
               res.set_content(R"({"error":"invalid json"})", "application/json");
               return;
             }
             std::string container = body.value("container", "mp4");
             std::optional<int> v, a, s;
             if (body.contains("video") && !body["video"].is_null())
               v = body["video"].get<int>();
             if (body.contains("audio") && !body["audio"].is_null())
               a = body["audio"].get<int>();
             if (body.contains("subtitle") && !body["subtitle"].is_null())
               s = body["subtitle"].get<int>();
             auto j = ffmpeg->remux_select(id, *path, work_root, container, v, a, s);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/cast",
           [require_media, ffmpeg, work_root](const httplib::Request& req,
                                              httplib::Response& res) {
             auto id = req.path_params.at("id");
             auto path = require_media(id, res);
             if (!path) return;
             std::string profile = "chromecast";
             if (req.has_param("profile")) profile = req.get_param_value("profile");
             if (!req.body.empty()) {
               try {
                 auto body = nlohmann::json::parse(req.body);
                 if (body.contains("profile"))
                   profile = body["profile"].get<std::string>();
               } catch (...) {
               }
             }
             auto j = ffmpeg->cast_export(id, *path, work_root, profile);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/waveform",
           [require_media, ffmpeg, work_root](const httplib::Request& req,
                                              httplib::Response& res) {
             auto id = req.path_params.at("id");
             auto path = require_media(id, res);
             if (!path) return;
             auto j = ffmpeg->waveform(id, *path, work_root);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Post("/v1/torrents/:id/chapters",
           [require_media, ffmpeg, work_root](const httplib::Request& req,
                                              httplib::Response& res) {
             auto id = req.path_params.at("id");
             auto path = require_media(id, res);
             if (!path) return;
             double threshold = 0.35;
             if (req.has_param("threshold"))
               threshold = std::stod(req.get_param_value("threshold"));
             if (!req.body.empty()) {
               try {
                 auto body = nlohmann::json::parse(req.body);
                 if (body.contains("threshold"))
                   threshold = body["threshold"].get<double>();
               } catch (...) {
               }
             }
             auto j = ffmpeg->scene_chapters(id, *path, work_root, threshold);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Get("/v1/torrents/:id/exports/:file",
          [ffmpeg, work_root](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto file = url_decode(req.path_params.at("file"));
            auto path = ffmpeg->export_file(id, file, work_root);
            if (!path) {
              res.status = 404;
              res.set_content(R"({"error":"export not found"})", "application/json");
              return;
            }
            std::ifstream in(*path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(in)),
                             std::istreambuf_iterator<char>());
            res.set_header("Cache-Control", "no-store");
            res.set_header(
                "Content-Disposition",
                "attachment; filename=\"" + file + "\"");
            res.set_content(std::move(body), mime_for_path(file));
          });

  svr.Post("/v1/torrents/:id/subtitle",
           [torrents, ffmpeg, work_root](const httplib::Request& req,
                                         httplib::Response& res) {
             auto id = req.path_params.at("id");
             if (!ffmpeg || !ffmpeg->available()) {
               res.status = 503;
               res.set_content(R"({"error":"ffmpeg missing"})", "application/json");
               return;
             }
             auto path = torrents->media_path(id);
             if (!path || !std::filesystem::exists(*path)) {
               res.status = 503;
               res.set_content(R"({"error":"media not ready"})", "application/json");
               return;
             }
             std::optional<int> idx;
             if (req.has_param("index") && !req.get_param_value("index").empty())
               idx = std::stoi(req.get_param_value("index"));
             auto j = ffmpeg->set_subtitle(id, *path, idx, work_root);
             if (j.contains("error")) res.status = 502;
             res.set_content(j.dump(), "application/json");
           });

  svr.Get("/v1/torrents/:id/hls/:file",
          [ffmpeg, work_root](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto file = url_decode(req.path_params.at("file"));
            auto path = ffmpeg->hls_file(id, file, work_root);
            if (!path) {
              res.status = 404;
              res.set_content(R"({"error":"not ready"})", "application/json");
              return;
            }
            std::ifstream in(*path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(in)),
                             std::istreambuf_iterator<char>());
            res.set_header("Cache-Control", "no-store");
            res.set_content(std::move(body), mime_for_path(file));
          });

  // ABR variant playlists / segments: /hls/0/index.m3u8, /hls/0/seg_00000.ts
  svr.Get("/v1/torrents/:id/hls/:dir/:file",
          [ffmpeg, work_root](const httplib::Request& req, httplib::Response& res) {
            auto id = req.path_params.at("id");
            auto dir = url_decode(req.path_params.at("dir"));
            auto file = url_decode(req.path_params.at("file"));
            if (dir.find("..") != std::string::npos ||
                file.find("..") != std::string::npos) {
              res.status = 400;
              res.set_content(R"({"error":"bad path"})", "application/json");
              return;
            }
            auto rel = dir + "/" + file;
            auto path = ffmpeg->hls_file(id, rel, work_root);
            if (!path) {
              res.status = 404;
              res.set_content(R"({"error":"not ready"})", "application/json");
              return;
            }
            std::ifstream in(*path, std::ios::binary);
            std::string body((std::istreambuf_iterator<char>(in)),
                             std::istreambuf_iterator<char>());
            res.set_header("Cache-Control", "no-store");
            res.set_content(std::move(body), mime_for_path(file));
          });

  svr.set_pre_routing_handler(
      [](const httplib::Request& req, httplib::Response& res) {
        // Browser test-player (and any 3rd-party page) is cross-origin vs
        // the engine port — CORS must be on every response, not only OPTIONS.
        res.set_header("Access-Control-Allow-Origin", "*");
        res.set_header("Access-Control-Allow-Methods",
                       "GET,POST,DELETE,OPTIONS");
        res.set_header("Access-Control-Allow-Headers", "Content-Type,Range");
        res.set_header("Access-Control-Expose-Headers",
                       "Accept-Ranges,Content-Range,Content-Length");
        if (req.method == "OPTIONS") {
          res.status = 204;
          return httplib::Server::HandlerResponse::Handled;
        }
        return httplib::Server::HandlerResponse::Unhandled;
      });
}

void HttpServer::run() {
  auto* holder = static_cast<ServerHolder*>(server_);
  if (cfg_.port == 0) {
    int p = holder->svr.bind_to_any_port(cfg_.bind.c_str());
    if (p < 0) {
      std::cerr << "Failed to bind\n";
      return;
    }
    port_ = p;
    std::cout << "ENGINE_READY port=" << port_ << std::endl;
    std::cout.flush();
    holder->svr.listen_after_bind();
  } else {
    port_ = cfg_.port;
    std::cout << "ENGINE_READY port=" << port_ << std::endl;
    std::cout.flush();
    holder->svr.listen(cfg_.bind.c_str(), port_);
  }
}

}  // namespace te
