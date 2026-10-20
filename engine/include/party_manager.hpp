#pragma once

#include <chrono>
#include <mutex>
#include <optional>
#include <string>
#include <unordered_map>
#include <vector>

#include <nlohmann/json.hpp>

namespace te {

// Watch-party control plane (shared clock). Media still flows over BitTorrent.
struct PartyRoom {
  std::string id;
  std::string magnet;
  std::string host_peer;       // optional "ip:port" for x.pe / /peers
  std::string torrent_id;      // host's local download id if any
  double position_sec = 0;
  bool paused = true;
  std::int64_t updated_ms = 0;
  std::string updated_by;
};

class PartyManager {
 public:
  nlohmann::json create(const std::string& magnet,
                        const std::optional<std::string>& host_peer,
                        const std::optional<std::string>& torrent_id,
                        const std::optional<std::string>& room_id);
  nlohmann::json get(const std::string& id);
  nlohmann::json list();
  nlohmann::json sync(const std::string& id,
                      double position_sec,
                      bool paused,
                      const std::string& who);
  bool remove(const std::string& id);

 private:
  std::mutex mu_;
  std::unordered_map<std::string, PartyRoom> rooms_;
};

}  // namespace te
