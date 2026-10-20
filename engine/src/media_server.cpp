#include "http_server.hpp"
#include "util.hpp"

#include <chrono>
#include <fstream>
#include <memory>
#include <thread>
#include <vector>

#define CPPHTTPLIB_NO_EXCEPTIONS
#include "httplib.h"

#include <libtorrent/torrent_info.hpp>
#include <libtorrent/torrent_status.hpp>

namespace te {
namespace {

bool pieces_ready(const lt::torrent_handle& h,
                  int file_index,
                  std::int64_t start,
                  std::int64_t end) {
  auto ti = h.torrent_file();
  if (!ti) return false;
  const auto& fs = ti->layout();
  const auto fidx = lt::file_index_t{file_index};
  const std::int64_t file_off = fs.file_offset(fidx);
  const int piece_length = ti->piece_length();
  if (piece_length <= 0) return false;
  const int first = static_cast<int>((file_off + start) / piece_length);
  const int last = static_cast<int>((file_off + end) / piece_length);
  for (int i = first; i <= last; ++i) {
    if (!h.have_piece(lt::piece_index_t{i})) return false;
  }
  return true;
}

bool wait_pieces(lt::torrent_handle& h,
                 int file_index,
                 std::int64_t start,
                 std::int64_t end,
                 int timeout_ms) {
  const auto deadline =
      std::chrono::steady_clock::now() + std::chrono::milliseconds(timeout_ms);
  while (std::chrono::steady_clock::now() < deadline) {
    if (!h.is_valid()) return false;
    if (!h.torrent_file()) {
      std::this_thread::sleep_for(std::chrono::milliseconds(100));
      continue;
    }
    if (pieces_ready(h, file_index, start, end)) return true;
    auto ti = h.torrent_file();
    const auto& fs = ti->layout();
    const std::int64_t file_off = fs.file_offset(lt::file_index_t{file_index});
    const int piece_length = ti->piece_length();
    const int first = static_cast<int>((file_off + start) / piece_length);
    const int last = static_cast<int>((file_off + end) / piece_length);
    for (int i = first; i <= last; ++i) {
      if (!h.have_piece(lt::piece_index_t{i})) {
        h.piece_priority(lt::piece_index_t{i}, lt::top_priority);
        h.set_piece_deadline(lt::piece_index_t{i}, 500);
      }
    }
    std::this_thread::sleep_for(std::chrono::milliseconds(150));
  }
  return pieces_ready(h, file_index, start, end);
}

}  // namespace

bool serve_media_range(TorrentManager& tm,
                       const std::string& id,
                       const std::string& /*range_header*/,
                       void* /*req*/,
                       void* res_void) {
  auto* res = static_cast<httplib::Response*>(res_void);
  auto job = tm.get(id);
  if (!job || !job->handle.is_valid()) {
    res->status = 404;
    res->set_content(R"({"error":"not found"})", "application/json");
    return true;
  }
  if (!job->handle.torrent_file() || job->media_file_index < 0) {
    res->status = 503;
    res->set_content(R"({"error":"torrent not ready","retry":true})",
                     "application/json");
    return true;
  }

  auto ti = job->handle.torrent_file();
  const auto fidx = lt::file_index_t{job->media_file_index};
  const std::int64_t total = ti->layout().file_size(fidx);
  const std::string fname = std::string(ti->layout().file_name(fidx));
  const auto path = tm.media_path(id);
  if (!path) {
    res->status = 503;
    res->set_content(R"({"error":"media path missing","retry":true})",
                     "application/json");
    return true;
  }
  if (total <= 0) {
    res->status = 503;
    res->set_content(R"({"error":"unknown size","retry":true})",
                     "application/json");
    return true;
  }

  // Warm the first 256KiB so the initial Range probe succeeds quickly.
  {
    auto handle = job->handle;
    const int file_index = job->media_file_index;
    wait_pieces(handle, file_index, 0,
                std::min<std::int64_t>(total - 1, 256 * 1024 - 1), 20000);
  }

  // Leave status at -1 so cpp-httplib can promote Range requests to 206 and
  // set a single Content-Range against the FULL content length. Do NOT pre-slice
  // the body or set Content-Range yourself — that produces duplicate
  // Content-Range headers (…/total and …/body.size) and browsers then 416.
  auto path_str = std::make_shared<std::filesystem::path>(*path);
  auto torrents = &tm;
  auto download_id = id;
  const int file_index = job->media_file_index;

  res->set_header("Accept-Ranges", "bytes");
  res->set_header("Cache-Control", "no-store");
  res->set_content_provider(
      static_cast<size_t>(total), mime_for_path(fname),
      [path_str, torrents, download_id, file_index, total](
          size_t offset, size_t length, httplib::DataSink& sink) {
        auto job = torrents->get(download_id);
        if (!job || !job->handle.is_valid()) return false;

        const std::int64_t start = static_cast<std::int64_t>(offset);
        const std::int64_t end =
            static_cast<std::int64_t>(offset + length) - 1;
        if (start < 0 || end >= total || start > end) return false;

        // Hint streaming playhead + wait for this chunk's pieces.
        torrents->set_playhead(download_id, start);
        if (!wait_pieces(job->handle, file_index, start, end, 20000)) {
          return false;
        }

        std::ifstream in(*path_str, std::ios::binary);
        if (!in) return false;
        in.seekg(start);
        std::vector<char> buf(std::min<size_t>(length, 256 * 1024));
        size_t left = length;
        while (left > 0) {
          const size_t n = std::min(left, buf.size());
          in.read(buf.data(), static_cast<std::streamsize>(n));
          const auto got = static_cast<size_t>(in.gcount());
          if (got == 0) break;
          if (!sink.write(buf.data(), got)) return false;
          left -= got;
          if (got < n) break;
        }
        return true;
      });

  return true;
}

}  // namespace te
