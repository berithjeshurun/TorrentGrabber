#include "updater.hpp"
#include "util.hpp"
#include "version.hpp"

#include <cstdlib>
#include <fstream>
#include <iostream>
#include <sstream>
#include <vector>

#include <openssl/evp.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif

namespace te {
namespace {

int ver_part(const std::string& s, size_t& i) {
  int v = 0;
  while (i < s.size() && s[i] >= '0' && s[i] <= '9') {
    v = v * 10 + (s[i] - '0');
    ++i;
  }
  if (i < s.size() && s[i] == '.') ++i;
  return v;
}

// semver-ish: a < b → -1, a==b → 0, a > b → 1
int cmp_version(const std::string& a, const std::string& b) {
  size_t ia = 0, ib = 0;
  for (int k = 0; k < 4; ++k) {
    int va = ver_part(a, ia);
    int vb = ver_part(b, ib);
    if (va < vb) return -1;
    if (va > vb) return 1;
  }
  return 0;
}

std::string sha256_hex(const std::string& data) {
  unsigned char hash[EVP_MAX_MD_SIZE];
  unsigned int len = 0;
  EVP_MD_CTX* ctx = EVP_MD_CTX_new();
  if (!ctx) return {};
  if (EVP_DigestInit_ex(ctx, EVP_sha256(), nullptr) != 1 ||
      EVP_DigestUpdate(ctx, data.data(), data.size()) != 1 ||
      EVP_DigestFinal_ex(ctx, hash, &len) != 1) {
    EVP_MD_CTX_free(ctx);
    return {};
  }
  EVP_MD_CTX_free(ctx);
  static const char* hex = "0123456789abcdef";
  std::string out;
  out.resize(len * 2);
  for (unsigned int i = 0; i < len; ++i) {
    out[i * 2] = hex[(hash[i] >> 4) & 0xf];
    out[i * 2 + 1] = hex[hash[i] & 0xf];
  }
  return out;
}

std::string platform_asset_key() {
#if defined(_WIN32) && defined(_M_X64)
  return "windows-x64";
#elif defined(_WIN32)
  return "windows-x86";
#elif defined(__APPLE__) && defined(__aarch64__)
  return "macos-arm64";
#elif defined(__APPLE__)
  return "macos-x64";
#elif defined(__linux__) && defined(__aarch64__)
  return "linux-arm64";
#else
  return "linux-x64";
#endif
}

bool write_file(const std::filesystem::path& p, const std::string& body) {
  std::ofstream out(p, std::ios::binary);
  if (!out) return false;
  out.write(body.data(), static_cast<std::streamsize>(body.size()));
  return static_cast<bool>(out);
}

#ifdef _WIN32
int spawn_windows_replacer(const std::filesystem::path& staging_dir,
                           const std::filesystem::path& target_dir,
                           DWORD pid) {
  auto bat = staging_dir / "apply_update.bat";
  {
    std::ofstream b(bat);
    if (!b) return 3;
    b << "@echo off\r\n"
      << "setlocal\r\n"
      << "set TARGET=" << target_dir.string() << "\r\n"
      << "set STAGE=" << staging_dir.string() << "\r\n"
      << "echo [update] waiting for pid " << pid << "...\r\n"
      << ":wait\r\n"
      << "tasklist /FI \"PID eq " << pid << "\" | find \"" << pid
      << "\" >nul\r\n"
      << "if not errorlevel 1 (\r\n"
      << "  timeout /t 1 /nobreak >nul\r\n"
      << "  goto wait\r\n"
      << ")\r\n"
      << "echo [update] copying files...\r\n"
      << "xcopy /Y /E /I /Q \"%STAGE%\\payload\\*\" \"%TARGET%\\\"\r\n"
      << "if errorlevel 1 (\r\n"
      << "  echo [update] copy failed\r\n"
      << "  exit /b 1\r\n"
      << ")\r\n"
      << "echo [update] done. You can restart torrent-engine.\r\n"
      << "endlocal\r\n";
  }
  std::string cmd = "cmd.exe /C \"" + bat.string() + "\"";
  STARTUPINFOA si{};
  si.cb = sizeof(si);
  PROCESS_INFORMATION pi{};
  std::vector<char> buf(cmd.begin(), cmd.end());
  buf.push_back('\0');
  if (!CreateProcessA(nullptr, buf.data(), nullptr, nullptr, FALSE,
                      CREATE_NEW_CONSOLE, nullptr, nullptr, &si, &pi)) {
    std::cerr << "[update] failed to spawn replacer\n";
    return 4;
  }
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  std::cerr << "[update] replacer started — exiting so files can be replaced\n";
  return 0;
}
#endif

}  // namespace

std::string resolve_manifest_url(const std::string& config_url) {
  if (const char* env = std::getenv("TORRENT_ENGINE_UPDATE_URL")) {
    if (env[0]) return env;
  }
  if (!config_url.empty()) return config_url;
  if (kDefaultUpdateManifestUrl[0]) return kDefaultUpdateManifestUrl;
  return {};
}

nlohmann::json version_json() {
  return nlohmann::json{
      {"name", "torrent-engine"},
      {"version", kEngineVersion},
      {"openapi", kOpenApiVersion},
      {"sdk_compat_min", kSdkCompatMin},
      {"platform", platform_asset_key()},
  };
}

UpdateCheckResult check_for_update(const std::string& manifest_url) {
  UpdateCheckResult r;
  r.current_version = kEngineVersion;
  if (manifest_url.empty()) {
    r.error =
        "No update manifest URL. Set update.manifest_url in config.yaml or "
        "TORRENT_ENGINE_UPDATE_URL.";
    return r;
  }
  std::string err;
  auto body = http_get_bytes(manifest_url, &err, 30);
  if (!body) {
    r.error = err.empty() ? "manifest fetch failed" : err;
    return r;
  }
  nlohmann::json j;
  try {
    j = nlohmann::json::parse(*body);
  } catch (const std::exception& ex) {
    r.error = std::string("invalid manifest JSON: ") + ex.what();
    return r;
  }
  r.ok = true;
  std::string latest = j.value("version", "");
  if (j.contains("engine") && j["engine"].is_object())
    latest = j["engine"].value("version", latest);
  r.latest_version = latest;
  r.notes = j.value("notes", "");
  if (j.contains("sdk") && j["sdk"].is_object()) r.sdk = j["sdk"];

  if (latest.empty()) {
    r.error = "manifest missing version";
    r.ok = false;
    return r;
  }
  r.update_available = cmp_version(kEngineVersion, latest) < 0;

  const std::string key = platform_asset_key();
  if (j.contains("assets") && j["assets"].is_object() &&
      j["assets"].contains(key) && j["assets"][key].is_object()) {
    auto& a = j["assets"][key];
    r.asset_url = a.value("url", "");
    r.asset_sha256 = a.value("sha256", "");
    r.asset_filename = a.value("filename", "torrent-engine-update.zip");
  }
  return r;
}

int apply_self_update(const UpdateCheckResult& check,
                      const std::filesystem::path& exe_path) {
  if (!check.ok) {
    std::cerr << "[update] " << check.error << "\n";
    return 2;
  }
  if (!check.update_available) {
    std::cerr << "[update] already on latest (" << check.current_version
              << ")\n";
    return 0;
  }
  if (check.asset_url.empty()) {
    std::cerr << "[update] update " << check.latest_version
              << " available but no asset url for " << platform_asset_key()
              << "\n"
              << "         Publish a release zip and set assets."
              << platform_asset_key() << ".url in the manifest.\n";
    return 2;
  }

  std::cerr << "[update] downloading " << check.asset_url << " …\n";
  std::string err;
  auto body = http_get_bytes(check.asset_url, &err, 300);
  if (!body) {
    std::cerr << "[update] download failed: " << err << "\n";
    return 3;
  }
  if (!check.asset_sha256.empty()) {
    auto got = sha256_hex(*body);
    std::string want = check.asset_sha256;
    for (char& c : want)
      c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (got != want) {
      std::cerr << "[update] sha256 mismatch\n  got  " << got << "\n  want "
                << want << "\n";
      return 4;
    }
  }

  auto target = exe_path.parent_path();
  if (target.empty()) target = std::filesystem::current_path();
  auto staging = target / ".update-staging";
  std::error_code ec;
  std::filesystem::remove_all(staging, ec);
  std::filesystem::create_directories(staging / "payload", ec);

  auto zip_path = staging / check.asset_filename;
  if (zip_path.extension().empty()) zip_path += ".zip";
  if (!write_file(zip_path, *body)) {
    std::cerr << "[update] cannot write " << zip_path.string() << "\n";
    return 5;
  }

#ifdef _WIN32
  // Expand with PowerShell (no extra zip lib).
  std::ostringstream ps;
  ps << "powershell -NoProfile -Command \"Expand-Archive -Force -Path '"
     << zip_path.string() << "' -DestinationPath '"
     << (staging / "payload").string() << "'\"";
  int zc = std::system(ps.str().c_str());
  if (zc != 0) {
    std::cerr << "[update] unzip failed (exit " << zc << ")\n";
    return 6;
  }
  return spawn_windows_replacer(staging, target, GetCurrentProcessId());
#else
  std::ostringstream cmd;
  cmd << "unzip -o '" << zip_path.string() << "' -d '"
      << (staging / "payload").string() << "'";
  if (std::system(cmd.str().c_str()) != 0) {
    std::cerr << "[update] unzip failed\n";
    return 6;
  }
  std::ostringstream cp;
  cp << "cp -a '" << (staging / "payload").string() << "/.' '"
     << target.string() << "/'";
  if (std::system(cp.str().c_str()) != 0) {
    std::cerr << "[update] copy failed — stop the engine and retry\n";
    return 7;
  }
  std::cerr << "[update] applied " << check.latest_version
            << " — restart torrent-engine\n";
  return 0;
#endif
}

int run_update_cli(bool apply, const std::string& manifest_url) {
  auto check = check_for_update(manifest_url);
  nlohmann::json out = version_json();
  out["manifest_url"] = manifest_url;
  out["ok"] = check.ok;
  out["update_available"] = check.update_available;
  out["latest_version"] = check.latest_version;
  out["notes"] = check.notes;
  out["asset_url"] = check.asset_url;
  if (!check.sdk.is_null()) out["sdk"] = check.sdk;
  if (!check.error.empty()) out["error"] = check.error;
  std::cout << out.dump(2) << "\n";

  if (!apply) return check.ok ? 0 : 1;
  if (!check.ok) return 1;

#ifdef _WIN32
  char path[MAX_PATH];
  DWORD n = GetModuleFileNameA(nullptr, path, MAX_PATH);
  std::filesystem::path exe = n ? std::filesystem::path(path)
                                : std::filesystem::path("torrent-engine.exe");
#else
  std::filesystem::path exe = "torrent-engine";
#endif
  int rc = apply_self_update(check, exe);
#ifdef _WIN32
  if (rc == 0 && check.update_available && !check.asset_url.empty()) {
    // Replacer needs this process to exit.
    std::exit(0);
  }
#endif
  return rc;
}

}  // namespace te
