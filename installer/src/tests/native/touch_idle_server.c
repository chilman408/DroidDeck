/* SPDX-License-Identifier: GPL-3.0-only */
/* Transport fixture: no uinput, Android calls or synthetic input. */
#include "touch_keepalive.h"
#include <stdlib.h>
#include <unistd.h>
#include <ws.h>
static void opened(ws_cli_conn_t *client) { (void)client; }
static void closed(ws_cli_conn_t *client) { (void)client; }
static void message(ws_cli_conn_t *client, const unsigned char *data, uint64_t size, int type) {
    (void)client;
    (void)data;
    (void)size;
    (void)type;
    _exit(3);
}
int main(int argc, char **argv) {
    if (argc != 2 || (argv[1][0] != '0' && argv[1][0] != '1') || argv[1][1]) return 2;
    struct ta_touch_keepalive keepalive;
    int enabled = argv[1][0] == '1';
    if (enabled && ta_touch_keepalive_start(&keepalive, 100) != 0) return 1;
    struct ws_events callbacks = {.onopen = opened, .onclose = closed, .onmessage = message};
    int result = ws_socket(&callbacks, 29999, 0, 1000);
    if (enabled) ta_touch_keepalive_stop(&keepalive);
    return result;
}
