cmake_minimum_required(VERSION 3.20)
project(torrent-engine VERSION 0.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(LibtorrentRasterbar CONFIG REQUIRED)
find_package(nlohmann_json CONFIG REQUIRED)
find_package(OpenSSL REQUIRED)
find_package(yaml-cpp CONFIG REQUIRED)

add_executable(torrent-engine
  src/main.cpp
  src/http_server.cpp
  src/torrent_manager.cpp
  src/media_server.cpp
  src/ffmpeg_bridge.cpp
  src/modes.cpp
  src/util.cpp
  src/config.cpp
  src/party_manager.cpp
  src/updater.cpp
)

target_include_directories(torrent-engine PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}/include
  ${CMAKE_CURRENT_SOURCE_DIR}/third_party
)

target_link_libraries(torrent-engine PRIVATE
  LibtorrentRasterbar::torrent-rasterbar
  nlohmann_json::nlohmann_json
  OpenSSL::SSL
  OpenSSL::Crypto
  yaml-cpp::yaml-cpp
)

if(WIN32)
  target_compile_definitions(torrent-engine PRIVATE
    _WIN32_WINNT=0x0A00
    WIN32_LEAN_AND_MEAN
    NOMINMAX
    TORRENT_USE_LIBCRYPTO
    CPPHTTPLIB_OPENSSL_SUPPORT
  )
  target_link_libraries(torrent-engine PRIVATE ws2_32 crypt32)
endif()

# Ship example config next to the binary
add_custom_command(TARGET torrent-engine POST_BUILD
  COMMAND ${CMAKE_COMMAND} -E copy_if_different
    ${CMAKE_CURRENT_SOURCE_DIR}/config.yaml
    $<TARGET_FILE_DIR:torrent-engine>/config.yaml
  COMMAND ${CMAKE_COMMAND} -E copy_directory
    ${CMAKE_CURRENT_SOURCE_DIR}/luts
    $<TARGET_FILE_DIR:torrent-engine>/luts
)

install(TARGETS torrent-engine RUNTIME DESTINATION bin)
install(FILES config.yaml DESTINATION bin)
install(DIRECTORY luts DESTINATION bin)
