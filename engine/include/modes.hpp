#pragma once

#include <cstdint>
#include <optional>
#include <string>
#include <vector>

#include <nlohmann/json.hpp>

namespace te {

enum class DownloadMode {
  General,
  Stream,
  Selective,
  Preview,
  Seed,
  Limited,
  Fastest,
  Mirror,
  Friend,  // private 1-on-1 share/receive
};

DownloadMode parse_mode(const std::string& s);
const char* mode_name(DownloadMode m);

struct ModeProfile {
  DownloadMode mode = DownloadMode::General;
  int connections_limit = 200;
  int active_downloads = 8;
  int download_rate_limit = 0;  // bytes/s, 0 = unlimited
  int upload_rate_limit = 0;
  bool sequential = false;
  bool private_swarm = false;  // disable DHT/LSD/PEX (friend / private)
  std::int64_t playhead_window_bytes = 8 * 1024 * 1024;
  std::int64_t lookahead_bytes = 24 * 1024 * 1024;
  std::int64_t preview_head_bytes = 16 * 1024 * 1024;
  std::int64_t preview_tail_bytes = 2 * 1024 * 1024;
  std::vector<int> selective_files;  // 0-based
};

ModeProfile profile_for(DownloadMode mode);

}  // namespace te
