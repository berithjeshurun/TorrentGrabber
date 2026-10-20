#include "party_manager.hpp"
#include "util.hpp"

#include <chrono>

namespace te {

static std::int64_t now_ms() {
  return std::chrono::duration_cast<std::chrono::milliseconds>(
             std::chrono::system_clock::now().time_since_epoch())
      .count();
}

static nlohmann::json room_json(const PartyRoom& r) {
  return nlohmann::json{
      {"id", r.id},
      {"magnet", r.magnet},
      {"host_peer", r.host_peer.empty() ? nullptr : nlohmann::json(r.host_peer)},
      {"torrent_id", r.torrent_id.empty() ? nullptr : nlohmann::json(r.torrent_id)},
      {"position_sec", r.position_sec},
      {"paused", r.paused},
      {"updated_ms", r.updated_ms},
      {"updated_by", r.updated_by},
      {"deep_link", "torrentgrabber://party/" + r.id},
  };
}

nlohmann::json PartyManager::create(const std::string& magnet,
                                    const std::optional<std::string>& host_peer,
                                    const std::optional<std::string>& torrent_id,
                                    const std::optional<std::string>& room_id) {
  if (magnet.empty()) return nlohmann::json{{"error", "magnet required"}};
  std::lock_guard lock(mu_);
  PartyRoom r;
  r.id = room_id.value_or(make_id());
  r.magnet = magnet;
  if (host_peer) r.host_peer = *host_peer;
  if (torrent_id) r.torrent_id = *torrent_id;
  r.position_sec = 0;
  r.paused = true;
  r.updated_ms = now_ms();
  r.updated_by = "host";
  rooms_[r.id] = r;
  return room_json(r);
}

nlohmann::json PartyManager::get(const std::string& id) {
  std::lock_guard lock(mu_);
  auto it = rooms_.find(id);
  if (it == rooms_.end()) return nlohmann::json{{"error", "not found"}};
  return room_json(it->second);
}

nlohmann::json PartyManager::list() {
  std::lock_guard lock(mu_);
  nlohmann::json arr = nlohmann::json::array();
  for (auto& [_, r] : rooms_) arr.push_back(room_json(r));
  return nlohmann::json{{"parties", arr}};
}

nlohmann::json PartyManager::sync(const std::string& id,
                                  double position_sec,
                                  bool paused,
                                  const std::string& who) {
  std::lock_guard lock(mu_);
  auto it = rooms_.find(id);
  if (it == rooms_.end()) return nlohmann::json{{"error", "not found"}};
  it->second.position_sec = std::max(0.0, position_sec);
  it->second.paused = paused;
  it->second.updated_ms = now_ms();
  it->second.updated_by = who.empty() ? "peer" : who;
  return room_json(it->second);
}

bool PartyManager::remove(const std::string& id) {
  std::lock_guard lock(mu_);
  return rooms_.erase(id) > 0;
}

}  // namespace te
