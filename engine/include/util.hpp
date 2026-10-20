#pragma once

#include <filesystem>
#include <optional>
#include <string>

namespace te {

std::string make_id();
std::optional<std::filesystem::path> find_ffmpeg_dir(
    const std::optional<std::filesystem::path>& override_dir);
std::filesystem::path ffmpeg_bin(const std::filesystem::path& dir, const char* name);
bool looks_like_video(const std::string& name);
std::string mime_for_path(const std::string& name);
std::string url_decode(const std::string& in);

// Fetch URL body (http/https). Returns nullopt + sets err_out on failure.
std::optional<std::string> http_get_bytes(const std::string& url,
                                          std::string* err_out = nullptr,
                                          int timeout_sec = 60);

// Best-effort public IPv4 via https://api.ipify.org (empty on failure).
std::string detect_public_ipv4(int timeout_sec = 5);

bool looks_like_http_url(const std::string& s);

// Containers that can be remuxed/piped before fully downloaded.
bool is_pipeable_media_name(const std::string& name);
// MP4/MOV need moov — prefer Range play or wait for completion for HLS.
bool is_seek_heavy_media_name(const std::string& name);

}  // namespace te
