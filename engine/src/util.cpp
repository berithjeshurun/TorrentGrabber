#include "util.hpp"

#include <cctype>
#include <cstdlib>
#include <cstring>
#include <chrono>
#include <random>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#endif

#include "httplib.h"

namespace te {

std::string make_id() {
  static thread_local std::mt19937_64 rng{
      static_cast<std::uint64_t>(
          std::chrono::high_resolution_clock::now().time_since_epoch().count())};
  std::uniform_int_distribution<int> dist(0, 15);
  static const char* hex = "0123456789abcdef";
  std::string out;
  out.resize(12);
  for (char& c : out) c = hex[dist(rng)];
  return out;
}

std::optional<std::filesystem::path> find_ffmpeg_dir(
    const std::optional<std::filesystem::path>& override_dir) {
  namespace fs = std::filesystem;
  std::vector<fs::path> candidates;
  if (override_dir) candidates.push_back(*override_dir);
  if (const char* env = std::getenv("TORRENT_ENGINE_FFMPEG")) {
    candidates.emplace_back(env);
  }
  // Sibling etc/ relative to cwd and common layouts
  candidates.push_back(fs::current_path() / "etc");
  candidates.push_back(fs::current_path() / "python" / "etc");
  candidates.push_back(fs::current_path().parent_path() / "python" / "etc");
  candidates.push_back(fs::current_path().parent_path() / "etc");

#ifdef _WIN32
  char buf[MAX_PATH];
  if (GetModuleFileNameA(nullptr, buf, MAX_PATH)) {
    fs::path exe = fs::path(buf).parent_path();
    candidates.push_back(exe / "etc");
    candidates.push_back(exe.parent_path() / "python" / "etc");
    candidates.push_back(exe.parent_path().parent_path() / "python" / "etc");
  }
#endif

  for (const auto& dir : candidates) {
    std::error_code ec;
    if (!fs::is_directory(dir, ec)) continue;
#ifdef _WIN32
    if (fs::is_regular_file(dir / "ffmpeg.exe", ec) &&
        fs::is_regular_file(dir / "ffprobe.exe", ec))
      return fs::absolute(dir);
#else
    if (fs::is_regular_file(dir / "ffmpeg", ec) &&
        fs::is_regular_file(dir / "ffprobe", ec))
      return fs::absolute(dir);
#endif
  }
  return std::nullopt;
}

std::filesystem::path ffmpeg_bin(const std::filesystem::path& dir, const char* name) {
#ifdef _WIN32
  return dir / (std::string(name) + ".exe");
#else
  return dir / name;
#endif
}

bool looks_like_video(const std::string& name) {
  auto lower = name;
  for (char& c : lower) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
  static const char* exts[] = {".mp4", ".webm", ".m4v", ".mov", ".mkv",
                               ".avi", ".ts",   ".m2ts", ".ogg", ".ogv"};
  for (auto* e : exts) {
    if (lower.size() >= strlen(e) &&
        lower.compare(lower.size() - strlen(e), strlen(e), e) == 0)
      return true;
  }
  return false;
}

std::string mime_for_path(const std::string& name) {
  auto lower = name;
  for (char& c : lower) c = static_cast<char>(tolower(static_cast<unsigned char>(c)));
  if (lower.ends_with(".mp4") || lower.ends_with(".m4v")) return "video/mp4";
  if (lower.ends_with(".webm")) return "video/webm";
  if (lower.ends_with(".mkv")) return "video/x-matroska";
  if (lower.ends_with(".mov")) return "video/quicktime";
  if (lower.ends_with(".m3u8")) return "application/vnd.apple.mpegurl";
  if (lower.ends_with(".ts")) return "video/mp2t";
  if (lower.ends_with(".vtt")) return "text/vtt";
  if (lower.ends_with(".json")) return "application/json";
  if (lower.ends_with(".png")) return "image/png";
  if (lower.ends_with(".jpg") || lower.ends_with(".jpeg")) return "image/jpeg";
  return "application/octet-stream";
}

std::string url_decode(const std::string& in) {
  std::string out;
  out.reserve(in.size());
  for (size_t i = 0; i < in.size(); ++i) {
    if (in[i] == '%' && i + 2 < in.size()) {
      auto hex = [](char c) -> int {
        if (c >= '0' && c <= '9') return c - '0';
        if (c >= 'a' && c <= 'f') return c - 'a' + 10;
        if (c >= 'A' && c <= 'F') return c - 'A' + 10;
        return -1;
      };
      int hi = hex(in[i + 1]);
      int lo = hex(in[i + 2]);
      if (hi >= 0 && lo >= 0) {
        out.push_back(static_cast<char>((hi << 4) | lo));
        i += 2;
        continue;
      }
    } else if (in[i] == '+') {
      out.push_back(' ');
      continue;
    }
    out.push_back(in[i]);
  }
  return out;
}

bool looks_like_http_url(const std::string& s) {
  return s.rfind("http://", 0) == 0 || s.rfind("https://", 0) == 0;
}

bool is_pipeable_media_name(const std::string& name) {
  std::string lower = name;
  for (char& c : lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return lower.ends_with(".mkv") || lower.ends_with(".webm") ||
         lower.ends_with(".avi") || lower.ends_with(".ts") ||
         lower.ends_with(".m2ts") || lower.ends_with(".mpg") ||
         lower.ends_with(".mpeg") || lower.ends_with(".vob");
}

bool is_seek_heavy_media_name(const std::string& name) {
  std::string lower = name;
  for (char& c : lower) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  return lower.ends_with(".mp4") || lower.ends_with(".m4v") ||
         lower.ends_with(".mov") || lower.ends_with(".m4a");
}

std::optional<std::string> http_get_bytes(const std::string& url,
                                          std::string* err_out,
                                          int timeout_sec) {
#ifndef CPPHTTPLIB_OPENSSL_SUPPORT
  if (url.rfind("https://", 0) == 0) {
    if (err_out) *err_out = "https fetch requires OpenSSL-enabled httplib build";
    return std::nullopt;
  }
#endif
  // Parse scheme://host[:port]/path
  std::string rest = url;
  bool https = false;
  if (rest.rfind("https://", 0) == 0) {
    https = true;
    rest = rest.substr(8);
  } else if (rest.rfind("http://", 0) == 0) {
    rest = rest.substr(7);
  } else {
    if (err_out) *err_out = "unsupported URL scheme";
    return std::nullopt;
  }
  auto slash = rest.find('/');
  std::string hostport = slash == std::string::npos ? rest : rest.substr(0, slash);
  std::string path = slash == std::string::npos ? "/" : rest.substr(slash);
  std::string host = hostport;
  int port = https ? 443 : 80;
  auto colon = hostport.rfind(':');
  if (colon != std::string::npos && hostport.find(']') == std::string::npos) {
    host = hostport.substr(0, colon);
    try {
      port = std::stoi(hostport.substr(colon + 1));
    } catch (...) {
      if (err_out) *err_out = "bad URL port";
      return std::nullopt;
    }
  }

#ifdef CPPHTTPLIB_OPENSSL_SUPPORT
  if (https) {
    httplib::SSLClient cli(host, port);
    cli.set_connection_timeout(timeout_sec, 0);
    cli.set_read_timeout(timeout_sec, 0);
    cli.set_follow_location(true);
    cli.enable_server_certificate_verification(false);
    auto res = cli.Get(path);
    if (!res) {
      if (err_out) *err_out = "https GET failed: " + httplib::to_string(res.error());
      return std::nullopt;
    }
    if (res->status < 200 || res->status >= 300) {
      if (err_out) *err_out = "HTTP " + std::to_string(res->status);
      return std::nullopt;
    }
    return res->body;
  }
#endif
  httplib::Client cli(host, port);
  cli.set_connection_timeout(timeout_sec, 0);
  cli.set_read_timeout(timeout_sec, 0);
  cli.set_follow_location(true);
  auto res = cli.Get(path);
  if (!res) {
    if (err_out) *err_out = "http GET failed: " + httplib::to_string(res.error());
    return std::nullopt;
  }
  if (res->status < 200 || res->status >= 300) {
    if (err_out) *err_out = "HTTP " + std::to_string(res->status);
    return std::nullopt;
  }
  return res->body;
}

std::string detect_public_ipv4(int timeout_sec) {
  std::string err;
  auto body = http_get_bytes("https://api.ipify.org", &err, timeout_sec);
  if (!body) return {};
  // trim
  std::string ip;
  for (char c : *body) {
    if ((c >= '0' && c <= '9') || c == '.') ip.push_back(c);
  }
  return ip;
}

}  // namespace te
