/* SPDX-License-Identifier: GPL-3.0-only */
#include <httplib.h>

#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <string>

static int parse_dimension(const char *text) {
    char *end = nullptr;
    errno = 0;
    long value = std::strtol(text, &end, 10);
    if (errno || end == text || *end || value < 1 || value > 8192) {
        return -1;
    }
    return static_cast<int>(value);
}

static void json(httplib::Response &response, const std::string &body) {
    response.set_header("Cache-Control", "no-store");
    response.set_content(body, "application/json");
}

static void unavailable(httplib::Response &response) {
    response.status = 501;
    json(response, R"({"error":"Not available in the x86 VM preview"})");
}

int main(int argc, char **argv) {
    if (argc != 3) {
        std::fprintf(stderr, "usage: %s WIDTH HEIGHT (1..8192)\n", argv[0]);
        return 2;
    }
    const int width = parse_dimension(argv[1]);
    const int height = parse_dimension(argv[2]);
    if (width < 0 || height < 0) {
        std::fprintf(stderr, "invalid display dimensions\n");
        return 2;
    }

    httplib::Server server;
    server.Get("/api/health", [](const httplib::Request &, httplib::Response &res) {
        json(res, R"({"status":"ok"})");
    });
    server.Get("/api/deviceInfo", [](const httplib::Request &, httplib::Response &res) {
        json(res, R"({"cpu_temperature":-1,"serial_number":"","device_model":"TeslaAndroid x86 VM preview","is_modem_detected":0,"is_carplay_detected":0,"release_type":"preview","ota_url":""})");
    });
    server.Get("/api/configuration", [](const httplib::Request &, httplib::Response &res) {
        json(res, R"({"persist.tesla-android.softap.band_type":0,"persist.tesla-android.softap.channel":0,"persist.tesla-android.softap.channel_width":0,"persist.tesla-android.softap.is_enabled":0,"persist.tesla-android.offline-mode.is_enabled":0,"persist.tesla-android.offline-mode.telemetry.is_enabled":0,"persist.tesla-android.offline-mode.tesla-firmware-downloads":0,"persist.tesla-android.browser_audio.is_enabled":0,"persist.tesla-android.browser_audio.volume":100,"persist.tesla-android.gps.is_active":0})");
    });
    server.Get("/api/displayState", [width, height](const httplib::Request &, httplib::Response &res) {
        const std::string body =
            "{\"width\":" + std::to_string(width) +
            ",\"height\":" + std::to_string(height) +
            R"(,"density":200,"resolutionPreset":0,"renderer":0,"isHeadless":0,"isResponsive":0,"isH264":0,"refreshRate":2,"quality":90,"isRearDisplayEnabled":0,"isRearDisplayPrioritised":0,"fixedDisplay":1})";
        json(res, body);
    });
    server.Post("/api/displayState", [](const httplib::Request &, httplib::Response &res) {
        unavailable(res);
    });
    server.Get("/api/openUpdater", [](const httplib::Request &, httplib::Response &res) {
        unavailable(res);
    });
    for (const char *path : {
             "/api/softApBand", "/api/softApChannel", "/api/softApChannelWidth",
             "/api/softApState", "/api/offlineModeState",
             "/api/offlineModeTelemetryState", "/api/offlineModeTeslaFirmwareDownloads",
             "/api/browserAudioState", "/api/browserAudioVolume", "/api/gpsState"}) {
        server.Post(path, [](const httplib::Request &, httplib::Response &res) {
            unavailable(res);
        });
    }
    std::fprintf(stderr, "x86 preview API on 127.0.0.1:8081 for %dx%d\n",
                 width, height);
    if (!server.listen("127.0.0.1", 8081)) {
        std::fprintf(stderr, "API bind failed\n");
        return 1;
    }
    return 0;
}
