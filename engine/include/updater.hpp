#pragma once

#include <filesystem>
#include <string>

#include <nlohmann/json.hpp>

namespace te {

struct UpdateCheckResult {
  bool ok = false;
  bool update_available = false;
  std::string current_version;
  std::string latest_version;
  std::string notes;
  std::string asset_url;
  std::string asset_sha256;
  std::string asset_filename;
  std::string error;
  nlohmann::json sdk;  // python / javascript / openapi versions from manifest
};

// Resolve manifest URL: env TORRENT_ENGINE_UPDATE_URL > config > default.
std::string resolve_manifest_url(const std::string& config_url);

UpdateCheckResult check_for_update(const std::string& manifest_url);

// Download asset, verify sha256 (if provided), stage next to exe, spawn
// Windows replace script and return. Returns 0 on success (process should exit).
int apply_self_update(const UpdateCheckResult& check,
                      const std::filesystem::path& exe_path);

// CLI entry: --check-update / --update
int run_update_cli(bool apply, const std::string& manifest_url);

nlohmann::json version_json();

}  // namespace te
