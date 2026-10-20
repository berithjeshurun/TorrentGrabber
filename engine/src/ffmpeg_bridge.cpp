#include "ffmpeg_bridge.hpp"
#include "util.hpp"

#include <algorithm>
#include <cctype>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <fstream>
#include <iostream>
#include <sstream>
#include <thread>
#include <vector>

#ifdef _WIN32
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#else
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

namespace te {
namespace {

std::string quote(const std::string& s) { return "\"" + s + "\""; }

std::string join_args(const std::vector<std::string>& args) {
  std::ostringstream cmd;
  for (size_t i = 0; i < args.size(); ++i) {
    if (i) cmd << ' ';
    if (args[i].find_first_of(" \t\"") != std::string::npos)
      cmd << '"' << args[i] << '"';
    else
      cmd << args[i];
  }
  return cmd.str();
}

#ifdef _WIN32
void* spawn_process(const std::vector<std::string>& args) {
  std::string cmdline = join_args(args);
  STARTUPINFOA si{};
  si.cb = sizeof(si);
  si.dwFlags = STARTF_USESHOWWINDOW;
  si.wShowWindow = SW_HIDE;
  PROCESS_INFORMATION pi{};
  std::vector<char> buf(cmdline.begin(), cmdline.end());
  buf.push_back('\0');
  DWORD flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB;
  if (!CreateProcessA(nullptr, buf.data(), nullptr, nullptr, FALSE, flags,
                      nullptr, nullptr, &si, &pi)) {
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
    if (!CreateProcessA(nullptr, buf.data(), nullptr, nullptr, FALSE, flags,
                        nullptr, nullptr, &si, &pi)) {
      return nullptr;
    }
  }
  CloseHandle(pi.hThread);
  return pi.hProcess;
}

bool wait_process(void* handle, int timeout_ms, DWORD* exit_code) {
  if (!handle) return false;
  DWORD w = WaitForSingleObject(reinterpret_cast<HANDLE>(handle),
                                timeout_ms < 0 ? INFINITE : timeout_ms);
  if (w != WAIT_OBJECT_0) return false;
  GetExitCodeProcess(reinterpret_cast<HANDLE>(handle), exit_code);
  return true;
}

void terminate_process(void* handle) {
  if (!handle) return;
  TerminateProcess(reinterpret_cast<HANDLE>(handle), 1);
  CloseHandle(reinterpret_cast<HANDLE>(handle));
}

void* spawn_cmd_redirect(const std::vector<std::string>& args,
                         const std::filesystem::path& log_path) {
  std::ostringstream wrapped;
  wrapped << "cmd.exe /C \"";
  for (size_t i = 0; i < args.size(); ++i) {
    if (i) wrapped << ' ';
    wrapped << quote(args[i]);
  }
  wrapped << " > " << quote(log_path.string()) << " 2>&1\"";
  std::string cmdline = wrapped.str();
  STARTUPINFOA si{};
  si.cb = sizeof(si);
  si.dwFlags = STARTF_USESHOWWINDOW;
  si.wShowWindow = SW_HIDE;
  PROCESS_INFORMATION pi{};
  std::vector<char> buf(cmdline.begin(), cmdline.end());
  buf.push_back('\0');
  DWORD flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB;
  if (!CreateProcessA(nullptr, buf.data(), nullptr, nullptr, FALSE, flags,
                      nullptr, nullptr, &si, &pi)) {
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
    if (!CreateProcessA(nullptr, buf.data(), nullptr, nullptr, FALSE, flags,
                        nullptr, nullptr, &si, &pi)) {
      return nullptr;
    }
  }
  CloseHandle(pi.hThread);
  return pi.hProcess;
}

// Spawn with inherited stdin pipe; *out_stdin_write receives the write end.
void* spawn_cmd_redirect_stdin(const std::vector<std::string>& args,
                               const std::filesystem::path& log_path,
                               HANDLE* out_stdin_write) {
  if (!out_stdin_write) return nullptr;
  SECURITY_ATTRIBUTES sa{};
  sa.nLength = sizeof(sa);
  sa.bInheritHandle = TRUE;
  HANDLE rd = nullptr, wr = nullptr;
  if (!CreatePipe(&rd, &wr, &sa, 1 << 20)) return nullptr;
  SetHandleInformation(wr, HANDLE_FLAG_INHERIT, 0);

  std::ostringstream wrapped;
  wrapped << "cmd.exe /C \"";
  for (size_t i = 0; i < args.size(); ++i) {
    if (i) wrapped << ' ';
    wrapped << quote(args[i]);
  }
  wrapped << " > " << quote(log_path.string()) << " 2>&1\"";
  std::string cmdline = wrapped.str();
  STARTUPINFOA si{};
  si.cb = sizeof(si);
  si.dwFlags = STARTF_USESHOWWINDOW | STARTF_USESTDHANDLES;
  si.wShowWindow = SW_HIDE;
  si.hStdInput = rd;
  si.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
  si.hStdError = GetStdHandle(STD_ERROR_HANDLE);
  PROCESS_INFORMATION pi{};
  std::vector<char> buf(cmdline.begin(), cmdline.end());
  buf.push_back('\0');
  DWORD flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB;
  BOOL ok = CreateProcessA(nullptr, buf.data(), nullptr, nullptr, TRUE, flags,
                           nullptr, nullptr, &si, &pi);
  if (!ok) {
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
    ok = CreateProcessA(nullptr, buf.data(), nullptr, nullptr, TRUE, flags,
                        nullptr, nullptr, &si, &pi);
  }
  CloseHandle(rd);
  if (!ok) {
    CloseHandle(wr);
    return nullptr;
  }
  CloseHandle(pi.hThread);
  *out_stdin_write = wr;
  return pi.hProcess;
}
#else
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <sys/stat.h>

extern char** environ;

void* spawn_process(const std::vector<std::string>& args) {
  if (args.empty()) return nullptr;
  std::vector<char*> argv;
  for (auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
  argv.push_back(nullptr);
  pid_t pid = 0;
  if (posix_spawnp(&pid, argv[0], nullptr, nullptr, argv.data(), environ) != 0)
    return nullptr;
  return reinterpret_cast<void*>(static_cast<intptr_t>(pid));
}

bool wait_process(void* handle, int timeout_ms, int* exit_code) {
  if (!handle) return false;
  pid_t pid = static_cast<pid_t>(reinterpret_cast<intptr_t>(handle));
  auto deadline = std::chrono::steady_clock::now() +
                  std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    int status = 0;
    pid_t r = waitpid(pid, &status, WNOHANG);
    if (r == pid) {
      if (exit_code) *exit_code = WIFEXITED(status) ? WEXITSTATUS(status) : 1;
      return true;
    }
    if (r < 0) return false;
    std::this_thread::sleep_for(std::chrono::milliseconds(50));
  }
  return false;
}

void terminate_process(void* handle) {
  if (!handle) return;
  pid_t pid = static_cast<pid_t>(reinterpret_cast<intptr_t>(handle));
  kill(pid, SIGTERM);
  waitpid(pid, nullptr, 0);
}

void* spawn_cmd_redirect(const std::vector<std::string>& args,
                         const std::filesystem::path& log_path) {
  if (args.empty()) return nullptr;
  int fd = open(log_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) return nullptr;
  posix_spawn_file_actions_t fa;
  posix_spawn_file_actions_init(&fa);
  posix_spawn_file_actions_adddup2(&fa, fd, STDOUT_FILENO);
  posix_spawn_file_actions_adddup2(&fa, fd, STDERR_FILENO);
  posix_spawn_file_actions_addclose(&fa, fd);
  std::vector<char*> argv;
  for (auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
  argv.push_back(nullptr);
  pid_t pid = 0;
  int rc = posix_spawnp(&pid, argv[0], &fa, nullptr, argv.data(), environ);
  posix_spawn_file_actions_destroy(&fa);
  close(fd);
  if (rc != 0) return nullptr;
  return reinterpret_cast<void*>(static_cast<intptr_t>(pid));
}

void* spawn_cmd_redirect_stdin(const std::vector<std::string>& args,
                               const std::filesystem::path& log_path,
                               int* out_stdin_write) {
  if (!out_stdin_write || args.empty()) return nullptr;
  int pipefd[2];
  if (pipe(pipefd) != 0) return nullptr;
  int fd = open(log_path.c_str(), O_WRONLY | O_CREAT | O_TRUNC, 0644);
  if (fd < 0) {
    close(pipefd[0]);
    close(pipefd[1]);
    return nullptr;
  }
  posix_spawn_file_actions_t fa;
  posix_spawn_file_actions_init(&fa);
  posix_spawn_file_actions_adddup2(&fa, pipefd[0], STDIN_FILENO);
  posix_spawn_file_actions_adddup2(&fa, fd, STDOUT_FILENO);
  posix_spawn_file_actions_adddup2(&fa, fd, STDERR_FILENO);
  posix_spawn_file_actions_addclose(&fa, pipefd[0]);
  posix_spawn_file_actions_addclose(&fa, pipefd[1]);
  posix_spawn_file_actions_addclose(&fa, fd);
  std::vector<char*> argv;
  for (auto& a : args) argv.push_back(const_cast<char*>(a.c_str()));
  argv.push_back(nullptr);
  pid_t pid = 0;
  int rc = posix_spawnp(&pid, argv[0], &fa, nullptr, argv.data(), environ);
  posix_spawn_file_actions_destroy(&fa);
  close(fd);
  close(pipefd[0]);
  if (rc != 0) {
    close(pipefd[1]);
    return nullptr;
  }
  *out_stdin_write = pipefd[1];
  return reinterpret_cast<void*>(static_cast<intptr_t>(pid));
}
#endif

bool looks_hdr(const std::string& transfer, const std::string& primaries) {
  auto t = transfer;
  auto p = primaries;
  for (char& c : t) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  for (char& c : p) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  if (t.find("smpte2084") != std::string::npos || t.find("pq") != std::string::npos)
    return true;
  if (t.find("arib-std-b67") != std::string::npos || t.find("hlg") != std::string::npos)
    return true;
  if (p.find("bt2020") != std::string::npos) return true;
  return false;
}

std::string transfer_for_zscale(const std::string& t) {
  std::string x = t;
  for (char& c : x) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  if (x.find("arib-std-b67") != std::string::npos || x.find("hlg") != std::string::npos)
    return "arib-std-b67";
  if (x.find("smpte2084") != std::string::npos || x.find("pq") != std::string::npos)
    return "smpte2084";
  return "bt709";
}

std::string primaries_for_zscale(const std::string& p) {
  std::string x = p;
  for (char& c : x) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  if (x.find("bt2020") != std::string::npos) return "bt2020";
  return "bt709";
}

std::string vtt_time(double sec) {
  if (sec < 0) sec = 0;
  int h = static_cast<int>(sec / 3600);
  int m = static_cast<int>(std::fmod(sec, 3600.0) / 60);
  double s = std::fmod(sec, 60.0);
  char buf[64];
  std::snprintf(buf, sizeof(buf), "%02d:%02d:%06.3f", h, m, s);
  return buf;
}

}  // namespace

FfmpegBridge::FfmpegBridge(std::filesystem::path ffmpeg_dir, HlsConfig hls_cfg)
    : dir_(std::move(ffmpeg_dir)), hls_(std::move(hls_cfg)) {}

bool FfmpegBridge::available() const {
  std::error_code ec;
  return std::filesystem::is_regular_file(ffmpeg_bin(dir_, "ffmpeg"), ec) &&
         std::filesystem::is_regular_file(ffmpeg_bin(dir_, "ffprobe"), ec);
}

void FfmpegBridge::kill_proc(void*& handle) {
  if (!handle) return;
  terminate_process(handle);
  handle = nullptr;
}

void FfmpegBridge::stop_pipe_feeder(HlsSession& sess) {
  if (sess.pipe_stop) sess.pipe_stop->store(true);
  if (sess.stdin_write) {
#ifdef _WIN32
    CloseHandle(reinterpret_cast<HANDLE>(sess.stdin_write));
#else
    close(static_cast<int>(reinterpret_cast<intptr_t>(sess.stdin_write)));
#endif
    sess.stdin_write = nullptr;
  }
  if (sess.pipe_thread.joinable()) {
    try {
      sess.pipe_thread.join();
    } catch (...) {
    }
  }
  sess.pipe_stop.reset();
}

bool FfmpegBridge::start_pipe_feeder(HlsSession& sess,
                                    const std::filesystem::path& media,
                                    std::int64_t start_offset) {
  if (!sess.stdin_write) return false;
  auto stop = std::make_shared<std::atomic<bool>>(false);
  sess.pipe_stop = stop;
  auto wait_fn = wait_range_;
  const std::string id = sess.download_id;
  const auto path = media;
#ifdef _WIN32
  HANDLE wr = reinterpret_cast<HANDLE>(sess.stdin_write);
  sess.pipe_thread = std::thread([stop, wr, wait_fn, id, path, start_offset]() {
#else
  int wr = static_cast<int>(reinterpret_cast<intptr_t>(sess.stdin_write));
  sess.pipe_thread = std::thread([stop, wr, wait_fn, id, path, start_offset]() {
#endif
    std::ifstream in(path, std::ios::binary);
    if (!in) {
#ifdef _WIN32
      CloseHandle(wr);
#else
      close(wr);
#endif
      return;
    }
    if (start_offset > 0) in.seekg(start_offset, std::ios::beg);
    std::int64_t offset = std::max<std::int64_t>(0, start_offset);
    constexpr std::size_t kChunk = 1024 * 1024;
    std::vector<char> buf(kChunk);
    while (!stop->load()) {
      if (wait_fn) {
        if (!wait_fn(id, offset, offset + static_cast<std::int64_t>(kChunk) - 1,
                     30000)) {
          if (stop->load()) break;
          for (int t = 0; t < 10 && !stop->load(); ++t) {
            if (wait_fn(id, offset,
                        offset + static_cast<std::int64_t>(kChunk) - 1, 15000))
              break;
          }
          if (stop->load()) break;
        }
      }
      in.read(buf.data(), static_cast<std::streamsize>(buf.size()));
      auto n = in.gcount();
      if (n <= 0) {
        bool grew = false;
        for (int t = 0; t < 40 && !stop->load(); ++t) {
          std::this_thread::sleep_for(std::chrono::milliseconds(250));
          in.clear();
          in.seekg(offset, std::ios::beg);
          in.read(buf.data(), static_cast<std::streamsize>(buf.size()));
          n = in.gcount();
          if (n > 0) {
            grew = true;
            break;
          }
        }
        if (!grew) break;
      }
      const char* p = buf.data();
      std::size_t left = static_cast<std::size_t>(n);
      while (left > 0 && !stop->load()) {
#ifdef _WIN32
        DWORD written = 0;
        if (!WriteFile(wr, p, static_cast<DWORD>(left), &written, nullptr) ||
            written == 0) {
          stop->store(true);
          break;
        }
#else
        ssize_t written = write(wr, p, left);
        if (written <= 0) {
          stop->store(true);
          break;
        }
#endif
        p += written;
        left -= static_cast<std::size_t>(written);
        offset += written;
      }
    }
#ifdef _WIN32
    CloseHandle(wr);
#else
    close(wr);
#endif
  });
  sess.stdin_write = nullptr;  // ownership moved to feeder thread
  return true;
}

bool FfmpegBridge::proc_alive(void* handle) {
  if (!handle) return false;
#ifdef _WIN32
  DWORD code = 0;
  if (!GetExitCodeProcess(reinterpret_cast<HANDLE>(handle), &code)) return false;
  return code == STILL_ACTIVE;
#else
  pid_t pid = static_cast<pid_t>(reinterpret_cast<intptr_t>(handle));
  int status = 0;
  pid_t r = waitpid(pid, &status, WNOHANG);
  if (r == 0) return true;
  return false;
#endif
}

void FfmpegBridge::finalize_playlist_if_dead(HlsSession& sess) {
  if (proc_alive(sess.video_proc)) return;
#ifdef _WIN32
  if (sess.video_proc) {
    CloseHandle(reinterpret_cast<HANDLE>(sess.video_proc));
    sess.video_proc = nullptr;
  }
#else
  sess.video_proc = nullptr;
#endif
  if (sess.finalized) return;

  auto append_endlist = [](const std::filesystem::path& p) {
    std::error_code ec;
    if (!std::filesystem::is_regular_file(p, ec)) return;
    std::ifstream in(p, std::ios::binary);
    std::string body((std::istreambuf_iterator<char>(in)),
                     std::istreambuf_iterator<char>());
    in.close();
    if (body.find("#EXT-X-ENDLIST") != std::string::npos) return;
    if (!body.empty() && body.back() != '\n') body.push_back('\n');
    body += "#EXT-X-ENDLIST\n";
    std::ofstream out(p, std::ios::binary | std::ios::trunc);
    out << body;
  };

  append_endlist(sess.playlist);
  if (sess.abr) {
    for (int i = 0; i < 3; ++i)
      append_endlist(sess.work_dir / std::to_string(i) / "index.m3u8");
  }
  // Also finalize default index when master exists
  append_endlist(sess.work_dir / "index.m3u8");
  sess.finalized = true;
}

bool FfmpegBridge::run_ffmpeg(const std::vector<std::string>& args,
                              std::string* err_out,
                              int timeout_ms) {
#ifdef _WIN32
  auto* proc = spawn_process(args);
  if (!proc) {
    if (err_out) *err_out = "failed to spawn ffmpeg";
    return false;
  }
  DWORD code = 1;
  if (!wait_process(proc, timeout_ms, &code)) {
    terminate_process(proc);
    if (err_out) *err_out = "ffmpeg timed out";
    return false;
  }
  CloseHandle(reinterpret_cast<HANDLE>(proc));
  if (code != 0) {
    if (err_out) *err_out = "ffmpeg exited with code " + std::to_string(code);
    return false;
  }
  return true;
#else
  (void)args;
  (void)timeout_ms;
  if (err_out) *err_out = "ffmpeg spawn not implemented on this platform yet";
  return false;
#endif
}

std::string FfmpegBridge::escape_lut_path(const std::string& path) {
  std::string out;
  for (char c : path) {
    if (c == '\\')
      out += '/';
    else if (c == ':' || c == '\'' || c == '[' || c == ']' || c == ',' || c == ';') {
      out += '\\';
      out += c;
    } else {
      out += c;
    }
  }
  return out;
}

std::string FfmpegBridge::build_vf(const ResolvedHls& r,
                                   const std::filesystem::path& media) const {
  std::vector<std::string> parts;
  if (r.apply_yadif) parts.push_back("yadif=0:-1:0");
  if (r.apply_tonemap) {
    auto tin = transfer_for_zscale(r.color_transfer);
    auto pin = primaries_for_zscale(r.color_primaries);
    auto min = pin;
    std::ostringstream tm;
    tm << "zscale=tin=" << tin << ":min=" << min << ":pin=" << pin
       << ":t=linear:n=linear,tonemap=" << r.tonemap_algo
       << ":desat=0,zscale=t=bt709:m=bt709:p=bt709,format=yuv420p";
    parts.push_back(tm.str());
  }
  if (!r.lut_cube.empty() && std::filesystem::exists(r.lut_cube)) {
    parts.push_back("lut3d='" + escape_lut_path(r.lut_cube) + "'");
  }
  if (r.burn_subtitle) {
    // Overlay absolute stream index onto video (works for PGS/VobSub bitmap).
    // When used, spawn uses filter_complex instead of -vf.
    (void)media;
  }
  if (parts.empty()) return {};
  std::string vf;
  for (size_t i = 0; i < parts.size(); ++i) {
    if (i) vf += ',';
    vf += parts[i];
  }
  return vf;
}

std::string FfmpegBridge::build_af(const ResolvedHls& r) const {
  if (r.loudnorm) return "loudnorm=I=-16:TP=-1.5:LRA=11";
  return {};
}

FfmpegBridge::ResolvedHls FfmpegBridge::resolve(const HlsRequest& req,
                                                const nlohmann::json& probe_j) const {
  ResolvedHls r;
  r.audio_bitrate = req.audio_bitrate.value_or(hls_.audio_bitrate);
  r.audio_channels = req.audio_channels.value_or(hls_.audio_channels);
  r.hls_time = hls_.hls_time > 0 ? hls_.hls_time : 2;
  r.low_latency = hls_.low_latency;
  r.loudnorm = req.loudnorm.value_or(hls_.loudnorm);
  r.deinterlace = req.deinterlace.value_or(hls_.deinterlace);
  r.tonemap = req.tonemap.value_or(hls_.tonemap);
  r.lut_cube = req.lut_cube.value_or(hls_.lut_cube);
  r.abr = req.abr.value_or(hls_.abr);
  r.audio_only = req.audio_only;
  r.audio_index = req.audio_index;
  r.start_seconds = req.start_seconds;
  r.burn_subtitle = req.burn_subtitle;
  if (req.input_url) r.input_url = *req.input_url;
  r.prefer_pipe = req.prefer_pipe;
  r.max_height = req.max_height;
  if (!r.burn_subtitle && hls_.burn_subs) {
    // leave unset unless API provides index
  }

  if (probe_j.contains("duration") && probe_j["duration"].is_number())
    r.duration = probe_j["duration"].get<double>();

  bool interlaced = probe_j.value("interlaced", false);
  bool hdr = probe_j.value("hdr", false);
  if (probe_j.contains("color_transfer") && probe_j["color_transfer"].is_string())
    r.color_transfer = probe_j["color_transfer"].get<std::string>();
  if (probe_j.contains("color_primaries") && probe_j["color_primaries"].is_string())
    r.color_primaries = probe_j["color_primaries"].get<std::string>();
  if (probe_j.contains("color_space") && probe_j["color_space"].is_string())
    r.color_space = probe_j["color_space"].get<std::string>();
  if (probe_j.contains("height") && probe_j["height"].is_number_integer())
    r.src_height = probe_j["height"].get<int>();

  if (probe_j.contains("video") && probe_j["video"].is_array() &&
      !probe_j["video"].empty()) {
    auto& v0 = probe_j["video"][0];
    std::string codec =
        v0.value("codec", v0.value("codec_name", std::string{}));
    for (char& c : codec)
      c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    r.video_hevc = (codec.find("hevc") != std::string::npos ||
                    codec.find("h265") != std::string::npos);
  } else if (probe_j.contains("streams") && probe_j["streams"].is_array()) {
    for (auto& s : probe_j["streams"]) {
      if (s.value("codec_type", "") != "video") continue;
      std::string codec = s.value("codec_name", "");
      for (char& c : codec)
        c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
      r.video_hevc = (codec.find("hevc") != std::string::npos ||
                      codec == "h265");
      break;
    }
  }
  // fMP4 + hvc1 tags help Safari/Chrome play HEVC remux without full transcode.
  r.use_fmp4 = r.video_hevc && !r.abr;

  std::string audio_codec;
  int audio_channels_n = 0;
  if (probe_j.contains("audio") && probe_j["audio"].is_array()) {
    const nlohmann::json* chosen = nullptr;
    for (auto& a : probe_j["audio"]) {
      if (r.audio_index && a.value("index", -1) == *r.audio_index) {
        chosen = &a;
        break;
      }
      if (!chosen) chosen = &a;
    }
    if (chosen) {
      audio_codec = chosen->value("codec", "");
      if (chosen->contains("channels") && (*chosen)["channels"].is_number())
        audio_channels_n = (*chosen)["channels"].get<int>();
    }
  }
  for (char& c : audio_codec)
    c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  const bool browser_audio =
      audio_codec == "aac" || audio_codec == "mp3";
  const bool keep_layout =
      r.audio_channels == "keep" || audio_channels_n <= 2;
  // Re-encoding AAC→AAC at 192k is what made playback sound worse.
  r.copy_audio = browser_audio && keep_layout && !r.loudnorm && !r.audio_only;

  if (r.deinterlace == "on" || (r.deinterlace == "auto" && interlaced))
    r.apply_yadif = true;

  std::string tm = r.tonemap;
  for (char& c : tm) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  if (tm == "off") {
    r.apply_tonemap = false;
  } else if (tm == "auto") {
    r.apply_tonemap = hdr;
    r.tonemap_algo = "hable";
  } else {
    r.apply_tonemap = hdr || tm == "hable" || tm == "mobius" || tm == "reinhard";
    if (tm == "hable" || tm == "mobius" || tm == "reinhard") r.tonemap_algo = tm;
    // Only apply when source is HDR — never invent HDR from SDR
    r.apply_tonemap = hdr;
  }

  bool has_lut = !r.lut_cube.empty() && std::filesystem::exists(r.lut_cube);
  r.force_transcode = r.apply_yadif || r.apply_tonemap || has_lut || r.burn_subtitle ||
                      r.abr || r.loudnorm;
  return r;
}

std::string FfmpegBridge::options_key(const ResolvedHls& r) const {
  std::ostringstream o;
  o << "a=" << (r.audio_index ? std::to_string(*r.audio_index) : "-")
    << ";ss=" << (r.start_seconds ? std::to_string(*r.start_seconds) : "-")
    << ";ao=" << r.audio_only << ";abr=" << r.abr
    << ";bs=" << (r.burn_subtitle ? std::to_string(*r.burn_subtitle) : "-")
    << ";br=" << r.audio_bitrate << ";ch=" << r.audio_channels
    << ";ln=" << r.loudnorm << ";di=" << r.deinterlace << ";tm=" << r.tonemap
    << ";lut=" << r.lut_cube << ";ht=" << r.hls_time
    << ";mh=" << r.max_height << ";fmp4=" << r.use_fmp4
    << ";pipe=" << r.prefer_pipe << ";acopy=" << r.copy_audio;
  return o.str();
}

nlohmann::json FfmpegBridge::session_json(const HlsSession& sess) const {
  nlohmann::json j;
  j["download_id"] = sess.download_id;
  std::string pl = sess.abr ? "master.m3u8" : "index.m3u8";
  j["playlist_url"] = "/v1/torrents/" + sess.download_id + "/hls/" + pl;
  j["audio_index"] =
      sess.audio_index ? nlohmann::json(*sess.audio_index) : nullptr;
  j["subtitle_index"] =
      sess.subtitle_index ? nlohmann::json(*sess.subtitle_index) : nullptr;
  j["start_seconds"] =
      sess.start_seconds ? nlohmann::json(*sess.start_seconds) : nullptr;
  j["audio_only"] = sess.audio_only;
  j["abr"] = sess.abr;
  j["burn_subtitle"] =
      sess.burn_subtitle ? nlohmann::json(*sess.burn_subtitle) : nullptr;
  j["ready"] = std::filesystem::exists(sess.playlist);
  j["encoder_alive"] = proc_alive(sess.video_proc);
  j["finalized"] = sess.finalized;
  if (sess.subtitle_index && std::filesystem::exists(sess.subtitle_vtt)) {
    j["subtitle_url"] = "/v1/torrents/" + sess.download_id + "/hls/subs.vtt";
  } else {
    j["subtitle_url"] = nullptr;
  }
  if (std::filesystem::exists(sess.poster)) {
    j["poster_url"] = "/v1/torrents/" + sess.download_id + "/poster.jpg";
  }
  return j;
}

nlohmann::json FfmpegBridge::probe(const std::filesystem::path& media) {
  auto probe = ffmpeg_bin(dir_, "ffprobe");
  if (!std::filesystem::is_regular_file(probe)) {
    return nlohmann::json{{"error", "ffprobe binary missing"}};
  }
  if (!std::filesystem::is_regular_file(media)) {
    return nlohmann::json{{"error", "media not on disk yet", "retry", true}};
  }
  auto out_path = std::filesystem::temp_directory_path() /
                  ("te-probe-" + make_id() + ".json");

#ifdef _WIN32
  auto bat_path = std::filesystem::temp_directory_path() /
                  ("te-probe-" + make_id() + ".bat");
  {
    std::ofstream bat(bat_path, std::ios::binary);
    bat << "@echo off\r\n";
    bat << quote(probe.string())
        << " -v error -print_format json -show_format -show_streams "
        << "-probesize 5M -analyzeduration 10M " << quote(media.string())
        << " > " << quote(out_path.string()) << " 2>nul\r\n";
  }
  std::string cmdline = "cmd.exe /C " + quote(bat_path.string());
  STARTUPINFOA si{};
  si.cb = sizeof(si);
  si.dwFlags = STARTF_USESHOWWINDOW;
  si.wShowWindow = SW_HIDE;
  PROCESS_INFORMATION pi{};
  std::vector<char> buf(cmdline.begin(), cmdline.end());
  buf.push_back('\0');
  DWORD flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB;
  if (!CreateProcessA(nullptr, buf.data(), nullptr, nullptr, FALSE, flags,
                      nullptr, nullptr, &si, &pi)) {
    flags = CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP;
    if (!CreateProcessA(nullptr, buf.data(), nullptr, nullptr, FALSE, flags,
                        nullptr, nullptr, &si, &pi)) {
      std::error_code ec;
      std::filesystem::remove(bat_path, ec);
      return nlohmann::json{{"error", "ffprobe spawn failed",
                             "winerr", (int)GetLastError()}};
    }
  }
  WaitForSingleObject(pi.hProcess, 90000);
  DWORD code = 1;
  GetExitCodeProcess(pi.hProcess, &code);
  CloseHandle(pi.hThread);
  CloseHandle(pi.hProcess);
  std::error_code ec;
  std::filesystem::remove(bat_path, ec);
  if (code != 0 || !std::filesystem::exists(out_path) ||
      std::filesystem::file_size(out_path) == 0) {
    std::filesystem::remove(out_path, ec);
    return nlohmann::json{{"error", "ffprobe failed", "exit_code", (int)code}};
  }
#else
  return nlohmann::json{{"error", "probe not implemented"}};
#endif

  std::ifstream in(out_path, std::ios::binary);
  nlohmann::json raw;
  try {
    in >> raw;
  } catch (...) {
    in.close();
    std::error_code ec2;
    std::filesystem::remove(out_path, ec2);
    return nlohmann::json{{"error", "invalid ffprobe json"}};
  }
  in.close();
  {
    std::error_code ec2;
    std::filesystem::remove(out_path, ec2);
  }

  if (!raw.is_object()) {
    return nlohmann::json{{"error", "ffprobe returned non-object json"}};
  }

  nlohmann::json out;
  auto format = raw.contains("format") && raw["format"].is_object()
                    ? raw["format"]
                    : nlohmann::json::object();
  out["format"] = format.value("format_name", "");
  auto dur = format.value("duration", "");
  try {
    out["duration"] = dur.empty() ? nullptr : nlohmann::json(std::stod(dur));
  } catch (...) {
    out["duration"] = nullptr;
  }
  out["ffmpeg"] = true;

  auto video = nlohmann::json::array();
  auto audio = nlohmann::json::array();
  auto subs = nlohmann::json::array();

  bool any_interlaced = false;
  bool any_hdr = false;
  std::string color_transfer, color_primaries, color_space;
  int height = 0;

  auto streams = raw.contains("streams") && raw["streams"].is_array()
                     ? raw["streams"]
                     : nlohmann::json::array();
  for (auto& s : streams) {
    if (!s.is_object()) continue;
    nlohmann::json t;
    t["index"] = s.value("index", 0);
    t["type"] = s.value("codec_type", "");
    t["codec"] = s.value("codec_name", "");
    auto tags = (s.contains("tags") && s["tags"].is_object())
                    ? s["tags"]
                    : nlohmann::json::object();
    t["language"] = tags.contains("language") ? tags["language"] : nullptr;
    t["title"] = tags.contains("title") ? tags["title"] : nullptr;
    t["channels"] = s.contains("channels") ? s["channels"] : nullptr;
    t["width"] = s.contains("width") ? s["width"] : nullptr;
    t["height"] = s.contains("height") ? s["height"] : nullptr;
    auto disp = (s.contains("disposition") && s["disposition"].is_object())
                    ? s["disposition"]
                    : nlohmann::json::object();
    t["default"] = disp.value("default", 0) != 0;
    t["forced"] = disp.value("forced", 0) != 0;
    std::string codec = t["codec"].is_string() ? t["codec"].get<std::string>() : "";
    bool bitmap = (codec == "hdmv_pgs_subtitle" || codec == "dvd_subtitle" ||
                   codec == "dvdsub" || codec == "pgssub" || codec == "xsub" ||
                   codec == "dvb_subtitle");
    if (t["type"] == "subtitle") t["text_based"] = !bitmap;

    std::string label;
    if (t["language"].is_string()) label += t["language"].get<std::string>();
    if (t["title"].is_string()) {
      if (!label.empty()) label += " · ";
      label += t["title"].get<std::string>();
    }
    if (!codec.empty()) {
      if (!label.empty()) label += " · ";
      label += codec;
    }
    t["label"] = label.empty() ? ("#" + std::to_string(t["index"].get<int>()))
                               : label;

    auto type = t["type"].is_string() ? t["type"].get<std::string>() : "";
    if (type == "video") {
      std::string fo = s.value("field_order", "unknown");
      bool interlaced = (fo != "progressive" && fo != "unknown" && !fo.empty());
      if (s.contains("field_order") &&
          (fo == "tt" || fo == "bb" || fo == "tb" || fo == "bt"))
        interlaced = true;
      t["field_order"] = fo;
      t["interlaced"] = interlaced;
      any_interlaced = any_interlaced || interlaced;

      std::string ct = s.value("color_transfer", "");
      std::string cp = s.value("color_primaries", "");
      std::string cs = s.value("color_space", "");
      t["color_transfer"] = ct.empty() ? nullptr : nlohmann::json(ct);
      t["color_primaries"] = cp.empty() ? nullptr : nlohmann::json(cp);
      t["color_space"] = cs.empty() ? nullptr : nlohmann::json(cs);
      bool hdr = looks_hdr(ct, cp);
      t["hdr"] = hdr;
      if (video.empty()) {
        color_transfer = ct;
        color_primaries = cp;
        color_space = cs;
        if (t["height"].is_number_integer()) height = t["height"].get<int>();
        any_hdr = hdr;
      }
      video.push_back(t);
    } else if (type == "audio") {
      audio.push_back(t);
    } else if (type == "subtitle") {
      subs.push_back(t);
    }
  }
  out["video"] = video;
  out["audio"] = audio;
  out["subtitles"] = subs;
  out["hdr"] = any_hdr;
  out["interlaced"] = any_interlaced;
  out["color_transfer"] =
      color_transfer.empty() ? nullptr : nlohmann::json(color_transfer);
  out["color_primaries"] =
      color_primaries.empty() ? nullptr : nlohmann::json(color_primaries);
  out["color_space"] = color_space.empty() ? nullptr : nlohmann::json(color_space);
  out["height"] = height > 0 ? nlohmann::json(height) : nullptr;

  std::string fmt = out["format"].is_string() ? out["format"].get<std::string>() : "";
  bool needs = fmt.find("matroska") != std::string::npos ||
               fmt.find("avi") != std::string::npos || audio.size() > 1 ||
               !subs.empty() || any_hdr || any_interlaced;
  out["needs_hls"] = needs;
  return out;
}

bool FfmpegBridge::spawn_hls(HlsSession& sess,
                             const std::filesystem::path& media,
                             const ResolvedHls& opts,
                             bool transcode,
                             std::string* err) {
  stop_pipe_feeder(sess);
  kill_proc(sess.video_proc);
  for (auto& p : std::filesystem::directory_iterator(sess.work_dir)) {
    auto name = p.path().filename().string();
    if (name == "subs.vtt" || name == "poster.jpg" || name == "thumbs") continue;
    std::error_code ec;
    std::filesystem::remove_all(p.path(), ec);
  }

  auto ff = ffmpeg_bin(dir_, "ffmpeg");
  std::vector<std::string> args;
  args.push_back(ff.string());
  args.insert(args.end(), {"-hide_banner", "-loglevel", "error", "-y"});
  // Tolerate sparse / incomplete torrent files (zeros in unread regions).
  args.insert(args.end(), {"-fflags", "+genpts+igndts+discardcorrupt",
                           "-err_detect", "ignore_err"});

  const bool pipeable =
      opts.prefer_pipe && is_pipeable_media_name(media.filename().string()) &&
      static_cast<bool>(wait_range_);
  const bool use_stdin_pipe = pipeable;
  const std::string input = use_stdin_pipe
                                ? "pipe:0"
                                : (!opts.input_url.empty() ? opts.input_url
                                                           : media.string());
  if (!use_stdin_pipe &&
      (input.rfind("http://", 0) == 0 || input.rfind("https://", 0) == 0)) {
    args.insert(args.end(),
                {"-reconnect", "1", "-reconnect_streamed", "1",
                 "-reconnect_on_network_error", "1", "-reconnect_delay_max",
                 "5"});
  }

  if (opts.start_seconds && *opts.start_seconds > 0.05) {
    args.insert(args.end(),
                {"-ss", std::to_string(*opts.start_seconds)});
  }
  // Format hint for pipe / sparse MKV-AVI-TS containers.
  if (pipeable || (opts.prefer_pipe &&
                   is_pipeable_media_name(media.filename().string()))) {
    std::string lower = media.extension().string();
    for (char& c : lower)
      c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
    if (lower == ".mkv" || lower == ".webm")
      args.insert(args.end(), {"-f", "matroska"});
    else if (lower == ".avi")
      args.insert(args.end(), {"-f", "avi"});
    else if (lower == ".ts" || lower == ".m2ts")
      args.insert(args.end(), {"-f", "mpegts"});
  }
  args.insert(args.end(), {"-i", input});

  bool need_filter_complex = opts.burn_subtitle.has_value() && !opts.audio_only;
  std::string vf = build_vf(opts, media);
  std::string af = build_af(opts);

  if (opts.audio_only) {
    if (opts.audio_index)
      args.insert(args.end(), {"-map", "0:" + std::to_string(*opts.audio_index)});
    else
      args.insert(args.end(), {"-map", "0:a:0?"});
    args.insert(args.end(), {"-vn", "-c:a", "aac", "-ar", "48000", "-b:a",
                             opts.audio_bitrate});
    if (opts.audio_channels == "stereo") args.insert(args.end(), {"-ac", "2"});
    if (!af.empty()) args.insert(args.end(), {"-af", af});
  } else if (need_filter_complex) {
    std::ostringstream fc;
    fc << "[0:v][0:" << *opts.burn_subtitle << "]overlay=format=auto";
    if (!vf.empty()) fc << "," << vf;
    else
      fc << ",format=yuv420p";
    fc << "[vout]";
    args.insert(args.end(), {"-filter_complex", fc.str(), "-map", "[vout]"});
    if (opts.audio_index)
      args.insert(args.end(), {"-map", "0:" + std::to_string(*opts.audio_index)});
    else
      args.insert(args.end(), {"-map", "0:a:0?"});
    args.insert(args.end(), {"-c:v", "libx264", "-preset", "ultrafast", "-tune",
                             "zerolatency", "-pix_fmt", "yuv420p"});
    args.insert(args.end(),
                {"-c:a", "aac", "-ar", "48000", "-b:a", opts.audio_bitrate});
    if (opts.audio_channels == "stereo") args.insert(args.end(), {"-ac", "2"});
    if (!af.empty()) args.insert(args.end(), {"-af", af});
  } else {
    args.insert(args.end(), {"-map", "0:v:0"});
    if (opts.audio_index)
      args.insert(args.end(), {"-map", "0:" + std::to_string(*opts.audio_index)});
    else
      args.insert(args.end(), {"-map", "0:a:0?"});

    if (transcode || opts.force_transcode) {
      if (!vf.empty()) args.insert(args.end(), {"-vf", vf});
      args.insert(args.end(), {"-c:v", "libx264", "-preset", "ultrafast", "-tune",
                               "zerolatency", "-pix_fmt", "yuv420p"});
    } else {
      args.insert(args.end(), {"-c:v", "copy"});
      if (opts.video_hevc)
        args.insert(args.end(), {"-tag:v", "hvc1"});
    }
    if (opts.copy_audio && af.empty()) {
      args.insert(args.end(), {"-c:a", "copy"});
    } else {
      args.insert(args.end(),
                  {"-c:a", "aac", "-ar", "48000", "-b:a", opts.audio_bitrate});
      if (opts.audio_channels == "stereo") args.insert(args.end(), {"-ac", "2"});
      if (!af.empty()) args.insert(args.end(), {"-af", af});
    }
  }

  std::string hls_flags = "independent_segments+append_list+omit_endlist";
  if (opts.low_latency) hls_flags = "independent_segments+omit_endlist";

  sess.playlist = sess.work_dir / "index.m3u8";
  sess.use_fmp4 = opts.use_fmp4 && !(transcode || opts.force_transcode);
  args.insert(args.end(),
              {"-f", "hls", "-hls_time", std::to_string(opts.hls_time),
               "-hls_list_size", "0", "-hls_flags", hls_flags});
  if (sess.use_fmp4) {
    args.insert(args.end(),
                {"-hls_segment_type", "fmp4", "-hls_fmp4_init_filename",
                 "init.mp4", "-hls_segment_filename",
                 (sess.work_dir / "seg_%05d.m4s").string()});
  } else {
    args.insert(args.end(),
                {"-hls_segment_filename",
                 (sess.work_dir / "seg_%05d.ts").string()});
  }
  args.push_back(sess.playlist.string());

  auto log_path = sess.work_dir / "ffmpeg-hls.log";
#ifdef _WIN32
  if (use_stdin_pipe) {
    HANDLE stdin_wr = nullptr;
    sess.video_proc = spawn_cmd_redirect_stdin(args, log_path, &stdin_wr);
    if (!sess.video_proc || !stdin_wr) {
      if (stdin_wr) CloseHandle(stdin_wr);
      if (err) *err = "failed to spawn ffmpeg hls (pipe)";
      return false;
    }
    sess.stdin_write = stdin_wr;
    if (!start_pipe_feeder(sess, media, 0)) {
      if (err) *err = "failed to start piece-wait pipe feeder";
      stop_pipe_feeder(sess);
      kill_proc(sess.video_proc);
      return false;
    }
  } else {
    sess.video_proc = spawn_cmd_redirect(args, log_path);
    if (!sess.video_proc) {
      if (err) *err = "failed to spawn ffmpeg hls";
      return false;
    }
  }

  bool wait_transcode = transcode || opts.force_transcode;
  for (int i = 0; i < (wait_transcode ? 120 : 40) * 4; ++i) {
    DWORD code = STILL_ACTIVE;
    GetExitCodeProcess(reinterpret_cast<HANDLE>(sess.video_proc), &code);
    if (std::filesystem::exists(sess.playlist) &&
        std::filesystem::file_size(sess.playlist) > 0)
      return true;
    if (code != STILL_ACTIVE) {
      std::string detail = "ffmpeg hls exited early";
      if (std::filesystem::exists(log_path)) {
        std::ifstream in(log_path);
        std::string log((std::istreambuf_iterator<char>(in)),
                        std::istreambuf_iterator<char>());
        if (!log.empty()) {
          if (log.size() > 400) log = log.substr(log.size() - 400);
          detail += ": " + log;
        }
      }
      if (err) *err = detail;
      stop_pipe_feeder(sess);
      CloseHandle(reinterpret_cast<HANDLE>(sess.video_proc));
      sess.video_proc = nullptr;
      return false;
    }
    Sleep(250);
  }
  if (std::filesystem::exists(sess.playlist)) return true;
  if (err) *err = "timed out waiting for HLS playlist";
  return false;
#else
  if (use_stdin_pipe) {
    int stdin_wr = -1;
    sess.video_proc = spawn_cmd_redirect_stdin(args, log_path, &stdin_wr);
    if (!sess.video_proc || stdin_wr < 0) {
      if (stdin_wr >= 0) close(stdin_wr);
      if (err) *err = "failed to spawn ffmpeg hls (pipe)";
      return false;
    }
    sess.stdin_write = reinterpret_cast<void*>(static_cast<intptr_t>(stdin_wr));
    if (!start_pipe_feeder(sess, media, 0)) {
      if (err) *err = "failed to start piece-wait pipe feeder";
      stop_pipe_feeder(sess);
      kill_proc(sess.video_proc);
      return false;
    }
  } else {
    sess.video_proc = spawn_cmd_redirect(args, log_path);
    if (!sess.video_proc) {
      if (err) *err = "failed to spawn ffmpeg hls";
      return false;
    }
  }
  bool wait_transcode = transcode || opts.force_transcode;
  for (int i = 0; i < (wait_transcode ? 120 : 40) * 4; ++i) {
    if (std::filesystem::exists(sess.playlist) &&
        std::filesystem::file_size(sess.playlist) > 0)
      return true;
    int code = 0;
    if (wait_process(sess.video_proc, 250, &code)) {
      std::string detail = "ffmpeg hls exited early";
      if (err) *err = detail;
      stop_pipe_feeder(sess);
      sess.video_proc = nullptr;
      return false;
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(250));
  }
  if (std::filesystem::exists(sess.playlist)) return true;
  if (err) *err = "timed out waiting for HLS playlist";
  return false;
#endif
}

bool FfmpegBridge::spawn_hls_abr(HlsSession& sess,
                                 const std::filesystem::path& media,
                                 const ResolvedHls& opts,
                                 std::string* err) {
  stop_pipe_feeder(sess);
  kill_proc(sess.video_proc);
  for (auto& p : std::filesystem::directory_iterator(sess.work_dir)) {
    auto name = p.path().filename().string();
    if (name == "subs.vtt" || name == "poster.jpg" || name == "thumbs") continue;
    std::error_code ec;
    std::filesystem::remove_all(p.path(), ec);
  }

  struct Rung {
    const char* name;
    int height;
    const char* bitrate;
  };
  // stream-server-style ladder: usable 360 → solid 720 → cinema 1080
  std::vector<Rung> rungs = {
      {"v360", 360, "1000k"}, {"v720", 720, "3200k"}, {"v1080", 1080, "6500k"}};
  if (opts.src_height > 0) {
    rungs.erase(std::remove_if(rungs.begin(), rungs.end(),
                               [&](const Rung& r) {
                                 return r.height > opts.src_height + 16;
                               }),
                rungs.end());
  }
  if (opts.max_height > 0) {
    rungs.erase(std::remove_if(rungs.begin(), rungs.end(),
                               [&](const Rung& r) {
                                 return r.height > opts.max_height;
                               }),
                rungs.end());
  }
  if (rungs.empty()) rungs.push_back({"v360", 360, "800k"});

  for (auto& r : rungs)
    std::filesystem::create_directories(sess.work_dir / r.name);

  auto ff = ffmpeg_bin(dir_, "ffmpeg");
  const std::string input =
      !opts.input_url.empty() ? opts.input_url : media.string();
  auto push_input = [&](std::vector<std::string>& args) {
    args.insert(args.end(), {"-fflags", "+genpts+igndts+discardcorrupt",
                             "-err_detect", "ignore_err"});
    if (input.rfind("http://", 0) == 0 || input.rfind("https://", 0) == 0) {
      args.insert(args.end(),
                  {"-reconnect", "1", "-reconnect_streamed", "1",
                   "-reconnect_on_network_error", "1", "-reconnect_delay_max",
                   "5"});
    }
    if (opts.start_seconds && *opts.start_seconds > 0.05)
      args.insert(args.end(), {"-ss", std::to_string(*opts.start_seconds)});
    args.insert(args.end(), {"-i", input});
  };
  std::vector<std::string> args = {ff.string(), "-hide_banner", "-loglevel",
                                   "error", "-y"};
  push_input(args);

  std::string base_vf = build_vf(opts, media);
  std::ostringstream fc;
  fc << "[0:v]";
  if (!base_vf.empty()) fc << base_vf << ",";
  fc << "split=" << rungs.size();
  for (size_t i = 0; i < rungs.size(); ++i) fc << "[s" << i << "]";
  fc << ";";
  for (size_t i = 0; i < rungs.size(); ++i) {
    fc << "[s" << i << "]scale=-2:" << rungs[i].height << "[o" << i << "]";
    if (i + 1 < rungs.size()) fc << ";";
  }
  args.insert(args.end(), {"-filter_complex", fc.str()});

  std::string var_map;
  for (size_t i = 0; i < rungs.size(); ++i) {
    if (i) var_map += " ";
    var_map += "v:" + std::to_string(i) + ",a:" + std::to_string(i);
    args.insert(args.end(), {"-map", "[o" + std::to_string(i) + "]"});
    if (opts.audio_index)
      args.insert(args.end(), {"-map", "0:" + std::to_string(*opts.audio_index)});
    else
      args.insert(args.end(), {"-map", "0:a:0?"});
  }

  args.insert(args.end(),
              {"-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-b:a",
               opts.audio_bitrate});
  if (opts.audio_channels == "stereo") args.insert(args.end(), {"-ac", "2"});
  auto af = build_af(opts);
  if (!af.empty()) args.insert(args.end(), {"-af", af});

  for (size_t i = 0; i < rungs.size(); ++i) {
    args.insert(args.end(),
                {"-b:v:" + std::to_string(i), rungs[i].bitrate});
  }

  std::string hls_flags = opts.low_latency
                              ? "independent_segments+omit_endlist"
                              : "independent_segments+append_list+omit_endlist";
  sess.playlist = sess.work_dir / "master.m3u8";
  args.insert(args.end(),
              {"-f", "hls", "-hls_time", std::to_string(opts.hls_time),
               "-hls_list_size", "0", "-hls_flags", hls_flags, "-master_pl_name",
               "master.m3u8", "-var_stream_map", var_map,
               "-hls_segment_filename",
               (sess.work_dir / "v%v" / "seg_%05d.ts").string(),
               (sess.work_dir / "v%v" / "index.m3u8").string()});

  // ffmpeg var_stream uses numeric %v — rename dirs after? Use stream index names.
  // Simpler: write to stream_%v and also generate master ourselves if needed.
  // Override paths to use numeric folders 0,1,2 matching var_stream.
  args.clear();
  args = {ff.string(), "-hide_banner", "-loglevel", "error", "-y"};
  push_input(args);
  args.insert(args.end(), {"-filter_complex", fc.str()});
  for (size_t i = 0; i < rungs.size(); ++i) {
    args.insert(args.end(), {"-map", "[o" + std::to_string(i) + "]"});
    if (opts.audio_index)
      args.insert(args.end(), {"-map", "0:" + std::to_string(*opts.audio_index)});
    else
      args.insert(args.end(), {"-map", "0:a:0?"});
  }
  args.insert(args.end(),
              {"-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000", "-b:a",
               opts.audio_bitrate});
  if (opts.audio_channels == "stereo") args.insert(args.end(), {"-ac", "2"});
  if (!af.empty()) args.insert(args.end(), {"-af", af});
  for (size_t i = 0; i < rungs.size(); ++i)
    args.insert(args.end(), {"-b:v:" + std::to_string(i), rungs[i].bitrate});

  for (size_t i = 0; i < rungs.size(); ++i)
    std::filesystem::create_directories(sess.work_dir / std::to_string(i));

  args.insert(args.end(),
              {"-f", "hls", "-hls_time", std::to_string(opts.hls_time),
               "-hls_list_size", "0", "-hls_flags", hls_flags, "-master_pl_name",
               "master.m3u8", "-var_stream_map", var_map,
               "-hls_segment_filename",
               (sess.work_dir / "%v" / "seg_%05d.ts").string(),
               (sess.work_dir / "%v" / "index.m3u8").string()});

#ifdef _WIN32
  auto log_path = sess.work_dir / "ffmpeg-hls-abr.log";
  sess.video_proc = spawn_cmd_redirect(args, log_path);
  if (!sess.video_proc) {
    if (err) *err = "failed to spawn ffmpeg abr hls";
    return false;
  }
  for (int i = 0; i < 180 * 4; ++i) {
    DWORD code = STILL_ACTIVE;
    GetExitCodeProcess(reinterpret_cast<HANDLE>(sess.video_proc), &code);
    if (std::filesystem::exists(sess.playlist) &&
        std::filesystem::file_size(sess.playlist) > 0)
      return true;
    // master may land next to first variant
    auto alt = sess.work_dir / "0" / "master.m3u8";
    if (std::filesystem::exists(alt)) {
      std::error_code ec;
      std::filesystem::rename(alt, sess.playlist, ec);
      if (std::filesystem::exists(sess.playlist)) return true;
    }
    if (code != STILL_ACTIVE) {
      std::string detail = "ffmpeg abr hls exited early";
      if (std::filesystem::exists(log_path)) {
        std::ifstream in(log_path);
        std::string log((std::istreambuf_iterator<char>(in)),
                        std::istreambuf_iterator<char>());
        if (log.size() > 400) log = log.substr(log.size() - 400);
        if (!log.empty()) detail += ": " + log;
      }
      if (err) *err = detail;
      CloseHandle(reinterpret_cast<HANDLE>(sess.video_proc));
      sess.video_proc = nullptr;
      return false;
    }
    Sleep(250);
  }
  if (std::filesystem::exists(sess.playlist)) return true;
  if (err) *err = "timed out waiting for ABR master playlist";
  return false;
#else
  if (err) *err = "ABR HLS not implemented";
  return false;
#endif
}

nlohmann::json FfmpegBridge::ensure_hls(const std::string& download_id,
                                        const std::filesystem::path& media,
                                        const HlsRequest& req,
                                        const std::filesystem::path& work_root) {
  auto probe_j = probe(media);
  if (probe_j.contains("error") && !probe_j["error"].is_null()) return probe_j;

  auto resolved = resolve(req, probe_j);
  auto key = options_key(resolved);

  std::lock_guard lock(mu_);
  auto it = sessions_.find(download_id);
  if (it != sessions_.end() && it->second.options_key == key &&
      std::filesystem::exists(it->second.playlist)) {
    finalize_playlist_if_dead(it->second);
    std::error_code ec;
    auto sz = std::filesystem::file_size(media, ec);
    const bool alive = proc_alive(it->second.video_proc);
    // Encoder finished early (common on incomplete/sparse torrent files). If the
    // media grew, restart so newly downloaded bytes get into the HLS ladder.
    const bool grew =
        !ec && sz > it->second.media_bytes + (2ull * 1024ull * 1024ull);
    if (alive || !grew) return session_json(it->second);
    // fall through to respawn
  }

  if (it != sessions_.end()) {
    stop_pipe_feeder(it->second);
    kill_proc(it->second.video_proc);
    // Keep poster/thumbs if present under work_dir — remove session work_dir fully
    std::error_code ec;
    std::filesystem::remove_all(it->second.work_dir, ec);
    sessions_.erase(it);
  }

  HlsSession sess;
  sess.download_id = download_id;
  sess.audio_index = resolved.audio_index;
  sess.start_seconds = resolved.start_seconds;
  sess.audio_only = resolved.audio_only;
  sess.abr = resolved.abr && !resolved.audio_only;
  sess.burn_subtitle = resolved.burn_subtitle;
  sess.options_key = key;
  {
    std::error_code ec;
    sess.media_bytes = std::filesystem::file_size(media, ec);
    if (ec) sess.media_bytes = 0;
  }
  sess.work_dir = work_root / ("hls-" + download_id + "-" + make_id());
  std::filesystem::create_directories(sess.work_dir);
  sess.subtitle_vtt = sess.work_dir / "subs.vtt";
  sess.poster = sess.work_dir / "poster.jpg";
  sess.thumbs_dir = sess.work_dir / "thumbs";
  sess.exports_dir = sess.work_dir / "exports";

  std::string err;
  bool ok = false;
  auto try_spawn = [&](ResolvedHls opts) {
    std::string e;
    bool success = false;
    if (sess.abr) {
      success = spawn_hls_abr(sess, media, opts, &e);
    } else {
      bool force = opts.force_transcode;
      if (force)
        success = spawn_hls(sess, media, opts, true, &e);
      else if (!spawn_hls(sess, media, opts, false, &e))
        success = spawn_hls(sess, media, opts, true, &e);
      else
        success = true;
    }
    err = e;
    return success;
  };

  ok = try_spawn(resolved);
  // Pipe path failed → fall back to HTTP /media input (piece-aware Range).
  if (!ok && resolved.prefer_pipe && !resolved.input_url.empty()) {
    auto fallback = resolved;
    fallback.prefer_pipe = false;
    ok = try_spawn(fallback);
    if (ok) sess.options_key = options_key(fallback) + ";fb=http";
  }
  if (!ok) {
    nlohmann::json e{{"error", err}};
    const bool transient =
        err.find("exited early") != std::string::npos ||
        err.find("prematurely") != std::string::npos ||
        err.find("Invalid data") != std::string::npos ||
        err.find("timed out") != std::string::npos ||
        err.find("pipe") != std::string::npos;
    if (transient) e["retry"] = true;
    e["pipe_fallback_attempted"] =
        resolved.prefer_pipe && !resolved.input_url.empty();
    return e;
  }

  sessions_[download_id] = std::move(sess);
  return session_json(sessions_[download_id]);
}

nlohmann::json FfmpegBridge::set_subtitle(const std::string& download_id,
                                          const std::filesystem::path& media,
                                          std::optional<int> subtitle_index,
                                          const std::filesystem::path& work_root) {
  std::lock_guard lock(mu_);
  auto it = sessions_.find(download_id);
  if (it == sessions_.end()) {
    HlsSession sess;
    sess.download_id = download_id;
    sess.work_dir = work_root / ("hls-" + download_id + "-" + make_id());
    std::filesystem::create_directories(sess.work_dir);
    sess.playlist = sess.work_dir / "index.m3u8";
    sess.subtitle_vtt = sess.work_dir / "subs.vtt";
    sess.poster = sess.work_dir / "poster.jpg";
    sess.thumbs_dir = sess.work_dir / "thumbs";
    sess.exports_dir = sess.work_dir / "exports";
    sessions_[download_id] = std::move(sess);
    it = sessions_.find(download_id);
  }

  auto& sess = it->second;
  sess.subtitle_index = subtitle_index;
  std::error_code ec;
  std::filesystem::remove(sess.subtitle_vtt, ec);

  if (!subtitle_index) {
    return nlohmann::json{{"download_id", download_id},
                          {"subtitle_index", nullptr},
                          {"subtitle_url", nullptr},
                          {"ready", false}};
  }

  auto ff = ffmpeg_bin(dir_, "ffmpeg");
  std::vector<std::string> args = {
      ff.string(),     "-hide_banner", "-loglevel", "error",
      "-err_detect",   "ignore_err",   "-i",        media.string(),
      "-map",          "0:" + std::to_string(*subtitle_index),
      "-c:s",          "webvtt",       "-y",        sess.subtitle_vtt.string()};

  std::string err;
  if (!run_ffmpeg(args, &err, 90000) || !std::filesystem::exists(sess.subtitle_vtt) ||
      std::filesystem::file_size(sess.subtitle_vtt) == 0) {
    args = {ff.string(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-err_detect",
            "ignore_err",
            "-i",
            media.string(),
            "-map",
            "0:" + std::to_string(*subtitle_index),
            "-f",
            "webvtt",
            "-y",
            sess.subtitle_vtt.string()};
    if (!run_ffmpeg(args, &err, 90000) || !std::filesystem::exists(sess.subtitle_vtt)) {
      return nlohmann::json{{"error", err.empty() ? "subtitle extract failed" : err},
                            {"ready", false}};
    }
  }

  {
    std::ifstream in(sess.subtitle_vtt);
    std::string text((std::istreambuf_iterator<char>(in)),
                     std::istreambuf_iterator<char>());
    if (text.find("WEBVTT") != 0) {
      std::ofstream out(sess.subtitle_vtt, std::ios::binary);
      out << "WEBVTT\n\n" << text;
    }
  }

  auto mtime = std::chrono::duration_cast<std::chrono::seconds>(
                   std::filesystem::last_write_time(sess.subtitle_vtt)
                       .time_since_epoch())
                   .count();

  return nlohmann::json{
      {"download_id", download_id},
      {"subtitle_index", *subtitle_index},
      {"subtitle_url",
       "/v1/torrents/" + download_id + "/hls/subs.vtt?t=" + std::to_string(mtime)},
      {"ready", true}};
}

nlohmann::json FfmpegBridge::ensure_poster(const std::string& download_id,
                                           const std::filesystem::path& media,
                                           const std::filesystem::path& work_root) {
  auto probe_j = probe(media);
  double duration = 0;
  if (probe_j.contains("duration") && probe_j["duration"].is_number())
    duration = probe_j["duration"].get<double>();
  double ss = duration > 1.0 ? duration * 0.10 : 1.0;

  std::lock_guard lock(mu_);
  auto it = sessions_.find(download_id);
  std::filesystem::path work;
  std::filesystem::path poster;
  if (it != sessions_.end()) {
    work = it->second.work_dir;
    poster = it->second.poster;
  } else {
    work = work_root / ("hls-" + download_id + "-" + make_id());
    std::filesystem::create_directories(work);
    poster = work / "poster.jpg";
    HlsSession sess;
    sess.download_id = download_id;
    sess.work_dir = work;
    sess.playlist = work / "index.m3u8";
    sess.subtitle_vtt = work / "subs.vtt";
    sess.poster = poster;
    sess.thumbs_dir = work / "thumbs";
    sess.exports_dir = work / "exports";
    sessions_[download_id] = std::move(sess);
  }

  if (std::filesystem::exists(poster) && std::filesystem::file_size(poster) > 0) {
    return nlohmann::json{
        {"download_id", download_id},
        {"poster_url", "/v1/torrents/" + download_id + "/poster.jpg"},
        {"ready", true}};
  }

  auto ff = ffmpeg_bin(dir_, "ffmpeg");
  std::vector<std::string> args = {
      ff.string(), "-hide_banner", "-loglevel", "error", "-y",
      "-ss",       std::to_string(ss),          "-i",    media.string(),
      "-frames:v", "1",                         "-q:v",  "2",
      poster.string()};
  std::string err;
  if (!run_ffmpeg(args, &err, 60000) || !std::filesystem::exists(poster)) {
    return nlohmann::json{{"error", err.empty() ? "poster failed" : err}};
  }
  return nlohmann::json{
      {"download_id", download_id},
      {"poster_url", "/v1/torrents/" + download_id + "/poster.jpg"},
      {"offset_seconds", ss},
      {"ready", true}};
}

nlohmann::json FfmpegBridge::ensure_thumbs(const std::string& download_id,
                                           const std::filesystem::path& media,
                                           const std::filesystem::path& work_root,
                                           int interval_sec) {
  if (interval_sec < 2) interval_sec = 2;
  auto probe_j = probe(media);
  double duration = 60;
  if (probe_j.contains("duration") && probe_j["duration"].is_number())
    duration = std::max(1.0, probe_j["duration"].get<double>());

  std::lock_guard lock(mu_);
  auto it = sessions_.find(download_id);
  std::filesystem::path thumbs;
  if (it != sessions_.end()) {
    thumbs = it->second.thumbs_dir;
  } else {
    auto work = work_root / ("hls-" + download_id + "-" + make_id());
    std::filesystem::create_directories(work);
    thumbs = work / "thumbs";
    HlsSession sess;
    sess.download_id = download_id;
    sess.work_dir = work;
    sess.playlist = work / "index.m3u8";
    sess.subtitle_vtt = work / "subs.vtt";
    sess.poster = work / "poster.jpg";
    sess.thumbs_dir = thumbs;
    sess.exports_dir = work / "exports";
    sessions_[download_id] = std::move(sess);
  }
  std::filesystem::create_directories(thumbs);
  auto sprite = thumbs / "sprite.jpg";
  auto vtt = thumbs / "thumbs.vtt";

  if (std::filesystem::exists(sprite) && std::filesystem::exists(vtt)) {
    return nlohmann::json{
        {"download_id", download_id},
        {"sprite_url", "/v1/torrents/" + download_id + "/thumbs/sprite.jpg"},
        {"vtt_url", "/v1/torrents/" + download_id + "/thumbs/thumbs.vtt"},
        {"interval_sec", interval_sec},
        {"ready", true}};
  }

  int cols = 10;
  int rows = static_cast<int>(std::ceil(duration / interval_sec / cols));
  if (rows < 1) rows = 1;
  if (rows > 20) rows = 20;

  auto ff = ffmpeg_bin(dir_, "ffmpeg");
  std::ostringstream vf;
  vf << "fps=1/" << interval_sec << ",scale=160:-1,tile=" << cols << "x" << rows;
  std::vector<std::string> args = {
      ff.string(), "-hide_banner", "-loglevel", "error", "-y",
      "-i",        media.string(), "-vf",       vf.str(), "-frames:v",
      "1",         "-q:v",         "3",         sprite.string()};
  std::string err;
  if (!run_ffmpeg(args, &err, 180000) || !std::filesystem::exists(sprite)) {
    return nlohmann::json{{"error", err.empty() ? "thumbs failed" : err}};
  }

  {
    std::ofstream out(vtt);
    out << "WEBVTT\n\n";
    int n = 0;
    for (double t = 0; t < duration && n < cols * rows; t += interval_sec, ++n) {
      int c = n % cols;
      int r = n / cols;
      double x = c * 160;
      double y = r * 90;  // approximate; clients may ignore xywh height
      out << n + 1 << "\n";
      out << vtt_time(t) << " --> " << vtt_time(t + interval_sec) << "\n";
      out << "sprite.jpg#xywh=" << static_cast<int>(x) << "," << static_cast<int>(y)
          << ",160,90\n\n";
    }
  }

  return nlohmann::json{
      {"download_id", download_id},
      {"sprite_url", "/v1/torrents/" + download_id + "/thumbs/sprite.jpg"},
      {"vtt_url", "/v1/torrents/" + download_id + "/thumbs/thumbs.vtt"},
      {"interval_sec", interval_sec},
      {"cols", cols},
      {"rows", rows},
      {"ready", true}};
}

std::optional<std::filesystem::path> FfmpegBridge::hls_file(
    const std::string& download_id,
    const std::string& filename,
    const std::filesystem::path& /*work_root*/) {
  std::lock_guard lock(mu_);
  auto it = sessions_.find(download_id);
  if (it == sessions_.end()) return std::nullopt;
  // Close out live playlists when ffmpeg has exited so hls.js stops waiting.
  if (filename.size() >= 5 &&
      filename.compare(filename.size() - 5, 5, ".m3u8") == 0) {
    finalize_playlist_if_dead(it->second);
  }
  if (filename.find("..") != std::string::npos) return std::nullopt;
  std::filesystem::path rel(filename);
  if (rel.is_absolute()) return std::nullopt;
  auto path = (it->second.work_dir / rel).lexically_normal();
  auto base = it->second.work_dir.lexically_normal();
  auto ps = path.string();
  auto bs = base.string();
  if (ps.size() < bs.size() || ps.compare(0, bs.size(), bs) != 0) return std::nullopt;
  if (!std::filesystem::exists(path)) return std::nullopt;
  return path;
}

std::optional<std::filesystem::path> FfmpegBridge::asset_file(
    const std::string& download_id,
    const std::string& kind,
    const std::string& filename,
    const std::filesystem::path& /*work_root*/) {
  std::lock_guard lock(mu_);
  auto it = sessions_.find(download_id);
  if (it == sessions_.end()) return std::nullopt;
  if (filename.find("..") != std::string::npos ||
      filename.find('/') != std::string::npos ||
      filename.find('\\') != std::string::npos)
    return std::nullopt;
  std::filesystem::path path;
  if (kind == "poster") {
    path = it->second.poster;
    if (filename != "poster.jpg" && !filename.empty()) return std::nullopt;
  } else if (kind == "thumbs") {
    path = it->second.thumbs_dir / filename;
  } else {
    return std::nullopt;
  }
  if (!std::filesystem::exists(path)) return std::nullopt;
  return path;
}

FfmpegBridge::HlsSession& FfmpegBridge::ensure_session_locked(
    const std::string& download_id,
    const std::filesystem::path& work_root) {
  auto it = sessions_.find(download_id);
  if (it != sessions_.end()) return it->second;
  HlsSession sess;
  sess.download_id = download_id;
  sess.work_dir = work_root / ("hls-" + download_id + "-" + make_id());
  std::filesystem::create_directories(sess.work_dir);
  sess.playlist = sess.work_dir / "index.m3u8";
  sess.subtitle_vtt = sess.work_dir / "subs.vtt";
  sess.poster = sess.work_dir / "poster.jpg";
  sess.thumbs_dir = sess.work_dir / "thumbs";
  sess.exports_dir = sess.work_dir / "exports";
  std::filesystem::create_directories(sess.exports_dir);
  sessions_[download_id] = std::move(sess);
  return sessions_[download_id];
}

std::filesystem::path FfmpegBridge::tool_bin(const char* name) const {
  return ffmpeg_bin(dir_, name);
}

bool FfmpegBridge::run_tool(const std::vector<std::string>& args,
                            std::string* err_out,
                            int timeout_ms) {
  return run_ffmpeg(args, err_out, timeout_ms);
}

nlohmann::json FfmpegBridge::silence_detect(const std::filesystem::path& media) {
  auto ff = tool_bin("ffmpeg");
  auto log_path = std::filesystem::temp_directory_path() /
                  ("te-silence-" + make_id() + ".log");
  nlohmann::json regions = nlohmann::json::array();
#ifdef _WIN32
  std::vector<std::string> args = {
      ff.string(),
      "-hide_banner",
      "-i",
      media.string(),
      "-af",
      "silencedetect=noise=-30dB:d=0.5",
      "-f",
      "null",
      "-"};
  auto* proc = spawn_cmd_redirect(args, log_path);
  if (!proc) return regions;
  DWORD code = 1;
  wait_process(proc, 180000, &code);
  CloseHandle(reinterpret_cast<HANDLE>(proc));
  if (!std::filesystem::exists(log_path)) return regions;
  std::ifstream in(log_path);
  std::string line;
  std::optional<double> start;
  while (std::getline(in, line)) {
    auto p = line.find("silence_start:");
    if (p != std::string::npos) {
      try {
        start = std::stod(line.substr(p + 14));
      } catch (...) {
        start.reset();
      }
    }
    p = line.find("silence_end:");
    if (p != std::string::npos && start) {
      try {
        // "silence_end: 12.3 | silence_duration: 1.2"
        auto rest = line.substr(p + 12);
        double end = std::stod(rest);
        double dur = end - *start;
        auto dpos = line.find("silence_duration:");
        if (dpos != std::string::npos) {
          try {
            dur = std::stod(line.substr(dpos + 17));
          } catch (...) {
          }
        }
        regions.push_back({{"start", *start}, {"end", end}, {"duration", dur}});
      } catch (...) {
      }
      start.reset();
    }
  }
  std::error_code ec;
  std::filesystem::remove(log_path, ec);
#else
  (void)media;
  (void)ff;
#endif
  return regions;
}

nlohmann::json FfmpegBridge::export_clip(const std::string& download_id,
                                         const std::filesystem::path& media,
                                         const std::filesystem::path& work_root,
                                         double start_sec,
                                         double end_sec,
                                         const std::string& container) {
  if (end_sec <= start_sec || start_sec < 0)
    return nlohmann::json{{"error", "invalid start/end"}};
  std::string ext = (container == "mkv") ? "mkv" : "mp4";
  std::lock_guard lock(mu_);
  auto& sess = ensure_session_locked(download_id, work_root);
  std::filesystem::create_directories(sess.exports_dir);
  auto out = sess.exports_dir / ("clip-" + make_id() + "." + ext);
  auto ff = tool_bin("ffmpeg");
  double dur = end_sec - start_sec;
  std::vector<std::string> args = {
      ff.string(), "-hide_banner", "-loglevel", "error", "-y",
      "-ss",       std::to_string(start_sec),   "-i",    media.string(),
      "-t",        std::to_string(dur),         "-map",  "0:v:0?",
      "-map",      "0:a:0?",                    "-c",    "copy"};
  if (ext == "mp4")
    args.insert(args.end(), {"-movflags", "+faststart", "-avoid_negative_ts",
                             "make_zero"});
  args.push_back(out.string());
  std::string err;
  if (!run_ffmpeg(args, &err, 300000) || !std::filesystem::exists(out)) {
    // Fallback remux with light re-encode for incompatible codecs
    args = {ff.string(),
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            std::to_string(start_sec),
            "-i",
            media.string(),
            "-t",
            std::to_string(dur),
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-b:a",
            "192k"};
    if (ext == "mp4") args.insert(args.end(), {"-movflags", "+faststart"});
    args.push_back(out.string());
    if (!run_ffmpeg(args, &err, 600000) || !std::filesystem::exists(out))
      return nlohmann::json{{"error", err.empty() ? "clip export failed" : err}};
  }
  return nlohmann::json{
      {"download_id", download_id},
      {"file", out.filename().string()},
      {"url", "/v1/torrents/" + download_id + "/exports/" + out.filename().string()},
      {"start", start_sec},
      {"end", end_sec},
      {"container", ext},
      {"bytes", std::filesystem::file_size(out)},
      {"ready", true}};
}

nlohmann::json FfmpegBridge::remux_select(
    const std::string& download_id,
    const std::filesystem::path& media,
    const std::filesystem::path& work_root,
    const std::string& container,
    std::optional<int> video_index,
    std::optional<int> audio_index,
    std::optional<int> subtitle_index) {
  std::string ext = (container == "mkv") ? "mkv" : "mp4";
  std::lock_guard lock(mu_);
  auto& sess = ensure_session_locked(download_id, work_root);
  std::filesystem::create_directories(sess.exports_dir);
  auto out = sess.exports_dir / ("remux-" + make_id() + "." + ext);
  auto ff = tool_bin("ffmpeg");
  std::vector<std::string> args = {ff.string(), "-hide_banner", "-loglevel",
                                   "error", "-y", "-i", media.string()};
  if (video_index)
    args.insert(args.end(), {"-map", "0:" + std::to_string(*video_index)});
  else
    args.insert(args.end(), {"-map", "0:v:0?"});
  if (audio_index)
    args.insert(args.end(), {"-map", "0:" + std::to_string(*audio_index)});
  else
    args.insert(args.end(), {"-map", "0:a:0?"});
  if (subtitle_index) {
    args.insert(args.end(), {"-map", "0:" + std::to_string(*subtitle_index)});
    if (ext == "mp4")
      args.insert(args.end(), {"-c:s", "mov_text"});
    else
      args.insert(args.end(), {"-c:s", "copy"});
  }
  args.insert(args.end(), {"-c:v", "copy", "-c:a", "copy"});
  if (ext == "mp4") args.insert(args.end(), {"-movflags", "+faststart"});
  args.push_back(out.string());

  std::string err;
  if (!run_ffmpeg(args, &err, 600000) || !std::filesystem::exists(out)) {
    return nlohmann::json{{"error", err.empty() ? "remux failed" : err}};
  }
  return nlohmann::json{
      {"download_id", download_id},
      {"file", out.filename().string()},
      {"url", "/v1/torrents/" + download_id + "/exports/" + out.filename().string()},
      {"container", ext},
      {"video_index", video_index ? nlohmann::json(*video_index) : nullptr},
      {"audio_index", audio_index ? nlohmann::json(*audio_index) : nullptr},
      {"subtitle_index",
       subtitle_index ? nlohmann::json(*subtitle_index) : nullptr},
      {"bytes", std::filesystem::file_size(out)},
      {"ready", true}};
}

nlohmann::json FfmpegBridge::cast_export(const std::string& download_id,
                                         const std::filesystem::path& media,
                                         const std::filesystem::path& work_root,
                                         const std::string& profile) {
  std::string p = profile;
  for (char& c : p) c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
  if (p != "chromecast" && p != "airplay")
    return nlohmann::json{{"error", "profile must be chromecast or airplay"}};

  std::lock_guard lock(mu_);
  auto& sess = ensure_session_locked(download_id, work_root);
  std::filesystem::create_directories(sess.exports_dir);
  auto out = sess.exports_dir / (p + "-" + make_id() + ".mp4");
  auto ff = tool_bin("ffmpeg");

  // Distinct encode caps: Chromecast (H.264 High@4.1 ≤1080p) vs AirPlay (≤4K, higher bitrates).
  const bool airplay = (p == "airplay");
  const char* scale =
      airplay
          ? "scale='min(3840,iw)':'-2':force_original_aspect_ratio=decrease,format=yuv420p"
          : "scale='min(1920,iw)':'-2':force_original_aspect_ratio=decrease,format=yuv420p";
  const char* maxrate = airplay ? "20M" : "8M";
  const char* bufsize = airplay ? "40M" : "16M";
  const char* level = airplay ? "5.1" : "4.1";
  const char* a_bitrate = airplay ? "256k" : "192k";

  std::vector<std::string> args = {
      ff.string(),
      "-hide_banner",
      "-loglevel",
      "error",
      "-y",
      "-i",
      media.string(),
      "-map",
      "0:v:0",
      "-map",
      "0:a:0?",
      "-vf",
      scale,
      "-c:v",
      "libx264",
      "-profile:v",
      "high",
      "-level",
      level,
      "-preset",
      "veryfast",
      "-crf",
      "23",
      "-maxrate",
      maxrate,
      "-bufsize",
      bufsize,
      "-c:a",
      "aac",
      "-profile:a",
      "aac_low",
      "-ac",
      "2",
      "-ar",
      "48000",
      "-b:a",
      a_bitrate,
      "-movflags",
      "+faststart",
      out.string()};

  std::string err;
  if (!run_ffmpeg(args, &err, 900000) || !std::filesystem::exists(out))
    return nlohmann::json{{"error", err.empty() ? "cast export failed" : err}};

  return nlohmann::json{
      {"download_id", download_id},
      {"profile", p},
      {"file", out.filename().string()},
      {"url", "/v1/torrents/" + download_id + "/exports/" + out.filename().string()},
      {"bytes", std::filesystem::file_size(out)},
      {"ready", true}};
}

nlohmann::json FfmpegBridge::waveform(const std::string& download_id,
                                      const std::filesystem::path& media,
                                      const std::filesystem::path& work_root) {
  std::lock_guard lock(mu_);
  auto& sess = ensure_session_locked(download_id, work_root);
  std::filesystem::create_directories(sess.exports_dir);
  auto json_out = sess.exports_dir / "waveform.json";
  auto png_out = sess.exports_dir / "waveform.png";

  auto aw = tool_bin("audiowaveform");
  nlohmann::json result{{"download_id", download_id}};

  if (std::filesystem::is_regular_file(aw)) {
    std::string err;
    std::vector<std::string> args = {
        aw.string(), "-i", media.string(), "-o", json_out.string(),
        "-z",        "256", "--pixels-per-second", "20"};
    if (!run_tool(args, &err, 300000) || !std::filesystem::exists(json_out)) {
      result["waveform_error"] = err.empty() ? "audiowaveform json failed" : err;
    } else {
      result["waveform_url"] =
          "/v1/torrents/" + download_id + "/exports/waveform.json";
    }
    args = {aw.string(), "-i", media.string(), "-o", png_out.string(),
            "-z",        "256", "--pixels-per-second", "20", "-w", "1200", "-h",
            "200"};
    if (run_tool(args, &err, 300000) && std::filesystem::exists(png_out)) {
      result["waveform_png_url"] =
          "/v1/torrents/" + download_id + "/exports/waveform.png";
    }
  } else {
    result["waveform_error"] = "audiowaveform.exe not found next to ffmpeg";
  }

  // Silence regions via ffmpeg (unlock briefly? silence_detect doesn't need lock
  // on sessions - but we hold lock. OK for sequential.)
  // Must call without re-locking — silence_detect doesn't take mu_
  auto silences = silence_detect(media);
  result["silences"] = silences;
  result["ready"] = result.contains("waveform_url") || !silences.empty();
  return result;
}

nlohmann::json FfmpegBridge::scene_chapters(
    const std::string& download_id,
    const std::filesystem::path& media,
    const std::filesystem::path& work_root,
    double threshold) {
  if (threshold <= 0 || threshold >= 1.0) threshold = 0.35;

  std::filesystem::path exports_dir;
  {
    std::lock_guard lock(mu_);
    auto& sess = ensure_session_locked(download_id, work_root);
    std::filesystem::create_directories(sess.exports_dir);
    exports_dir = sess.exports_dir;
  }

  auto ff = tool_bin("ffmpeg");
  auto meta = exports_dir / ("scenes-" + make_id() + ".txt");
  std::string meta_esc = escape_lut_path(meta.string());
  std::ostringstream vf;
  vf << "select='gt(scene\\," << threshold << ")',metadata=print:file="
     << meta_esc;

  std::vector<std::string> args = {
      ff.string(), "-hide_banner", "-loglevel", "info", "-i", media.string(),
      "-vf",       vf.str(),       "-an",       "-f",   "null", "-"};
  std::string err;
  run_ffmpeg(args, &err, 600000);

  std::vector<double> times;
  times.push_back(0.0);
  if (std::filesystem::exists(meta)) {
    std::ifstream in(meta);
    std::string line;
    while (std::getline(in, line)) {
      auto p = line.find("pts_time:");
      if (p == std::string::npos) p = line.find("pts_time=");
      if (p != std::string::npos) {
        try {
          size_t skip = (line[p + 8] == ':' || line[p + 8] == '=') ? 9 : 9;
          double t = std::stod(line.substr(p + skip));
          if (t - times.back() > 1.0) times.push_back(t);
        } catch (...) {
        }
      }
    }
  }

  // Fallback: parse ffmpeg stderr log via redirected run if still only chapter 0
  if (times.size() <= 1) {
#ifdef _WIN32
    auto log_path = exports_dir / ("scenes-log-" + make_id() + ".txt");
    std::ostringstream vf2;
    vf2 << "select='gt(scene\\," << threshold << ")',showinfo";
    std::vector<std::string> args2 = {
        ff.string(), "-hide_banner", "-i", media.string(), "-vf", vf2.str(),
        "-an",       "-f",           "null", "-"};
    auto* proc = spawn_cmd_redirect(args2, log_path);
    if (proc) {
      DWORD code = 1;
      wait_process(proc, 600000, &code);
      CloseHandle(reinterpret_cast<HANDLE>(proc));
      if (std::filesystem::exists(log_path)) {
        std::ifstream in(log_path);
        std::string line;
        while (std::getline(in, line)) {
          auto p = line.find("pts_time:");
          if (p != std::string::npos) {
            try {
              double t = std::stod(line.substr(p + 9));
              if (t - times.back() > 1.0) times.push_back(t);
            } catch (...) {
            }
          }
        }
      }
    }
#endif
  }

  nlohmann::json chapters = nlohmann::json::array();
  for (size_t i = 0; i < times.size(); ++i) {
    double start = times[i];
    nlohmann::json ch;
    ch["index"] = static_cast<int>(i);
    ch["start"] = start;
    if (i + 1 < times.size()) ch["end"] = times[i + 1];
    ch["title"] = "Scene " + std::to_string(i + 1);
    chapters.push_back(ch);
  }

  auto out = exports_dir / "chapters.json";
  {
    nlohmann::json doc{{"chapters", chapters}, {"threshold", threshold}};
    std::ofstream o(out);
    o << doc.dump(2);
  }

  return nlohmann::json{
      {"download_id", download_id},
      {"chapters", chapters},
      {"count", chapters.size()},
      {"threshold", threshold},
      {"url", "/v1/torrents/" + download_id + "/exports/chapters.json"},
      {"ready", true}};
}

std::optional<std::filesystem::path> FfmpegBridge::export_file(
    const std::string& download_id,
    const std::string& filename,
    const std::filesystem::path& /*work_root*/) {
  std::lock_guard lock(mu_);
  auto it = sessions_.find(download_id);
  if (it == sessions_.end()) return std::nullopt;
  if (filename.find("..") != std::string::npos ||
      filename.find('/') != std::string::npos ||
      filename.find('\\') != std::string::npos)
    return std::nullopt;
  auto path = it->second.exports_dir / filename;
  if (!std::filesystem::exists(path)) return std::nullopt;
  return path;
}

}  // namespace te
