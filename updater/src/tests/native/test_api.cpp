/* SPDX-License-Identifier: GPL-3.0-only */
#define main droiddeck_api_unused_main
#include "../../runtime/api/tesla-android-x86-api.cpp"
#undef main
#include <cassert>

int main() {
    assert(parse_dimension("1") == 1);
    assert(parse_dimension("8192") == 8192);
    assert(parse_dimension("1920") == 1920);
    for (const char *text : {"", "0", "-1", "8193", "1x", "1 ", "1.0",
             "999999999999999999999999999999999999999999999"}) {
        assert(parse_dimension(text) == -1);
    }
    httplib::Response response;
    json(response, "{\"generated\":true}");
    assert(response.body == "{\"generated\":true}");
    assert(response.get_header_value("Cache-Control") == "no-store");
    assert(response.get_header_value("Content-Type") == "application/json");
    httplib::Response disabled;
    unavailable(disabled);
    assert(disabled.status == 501);
    assert(disabled.body == "{\"error\":\"Not available in the x86 VM preview\"}");
    assert(disabled.get_header_value("Cache-Control") == "no-store");
    std::puts("generated API tests passed; no server or sockets started");
    return 0;
}
