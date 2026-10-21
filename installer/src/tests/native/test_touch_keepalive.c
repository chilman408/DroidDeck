/* SPDX-License-Identifier: GPL-3.0-only */
#include "touch_keepalive.h"
#include <assert.h>
#include <stdatomic.h>
#include <time.h>
#include <ws.h>
static atomic_int pings;
void ws_ping(ws_cli_conn_t *client, int threshold) {
    assert(client == NULL);
    assert(threshold == 3);
    atomic_fetch_add(&pings, 1);
}
static void pause_ms(long ms) {
    struct timespec delay = {ms / 1000, (ms % 1000) * 1000000};
    nanosleep(&delay, NULL);
}
int main(void) {
    struct ta_touch_keepalive keepalive;
    assert(ta_touch_keepalive_start(&keepalive, 0) != 0);
    assert(ta_touch_keepalive_start(&keepalive, 60001) != 0);
    assert(ta_touch_keepalive_start(&keepalive, 20) == 0);
    for (int i = 0; i < 100 && atomic_load(&pings) < 3; ++i) pause_ms(5);
    ta_touch_keepalive_stop(&keepalive);
    int count = atomic_load(&pings);
    assert(count >= 3);
    pause_ms(50);
    assert(atomic_load(&pings) == count);
    /* Stop wakes the timer; shutdown must not wait a full production interval. */
    assert(ta_touch_keepalive_start(&keepalive, 15000) == 0);
    struct timespec before, after;
    clock_gettime(CLOCK_MONOTONIC, &before);
    ta_touch_keepalive_stop(&keepalive);
    clock_gettime(CLOCK_MONOTONIC, &after);
    assert((after.tv_sec - before.tv_sec) * 1000 + (after.tv_nsec - before.tv_nsec) / 1000000 < 1000);
    assert(atomic_load(&pings) == count);
    return 0;
}
