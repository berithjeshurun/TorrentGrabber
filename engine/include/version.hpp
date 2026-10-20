#pragma once

namespace te {

// Keep in sync with repo VERSION / update/manifest.json / openapi info.version
inline constexpr const char* kEngineVersion = "0.1.0";
inline constexpr const char* kOpenApiVersion = "0.1.0";
inline constexpr const char* kSdkCompatMin = "0.1.0";

// Default manifest URL — override via config update.manifest_url or
// env TORRENT_ENGINE_UPDATE_URL.
inline constexpr const char* kDefaultUpdateManifestUrl =
    "https://raw.githubusercontent.com/berithjeshurun/TorrentGrabber/main/"
    "update/manifest.json";

}  // namespace te
