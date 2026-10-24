/* SPDX-License-Identifier: GPL-3.0-only */
#include "../../runtime/touch/touch_protocol.h"

#include <assert.h>
#include <linux/input.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void translate(struct ta_touch_state *state, const char *message,
                      struct ta_touch_event *events, size_t *count) {
    struct ta_touch_state next;
    assert(ta_touch_translate(state, (const uint8_t *)message, strlen(message),
                              1920, 1080, &next, events, TA_TOUCH_MAX_EVENTS,
                              count) == 0);
    *state = next;
}

static void invalid(struct ta_touch_state *state, const char *message) {
    struct ta_touch_state next;
    struct ta_touch_event events[TA_TOUCH_MAX_EVENTS];
    size_t count = 0;
    assert(ta_touch_translate(state, (const uint8_t *)message, strlen(message),
                              1920, 1080, &next, events, TA_TOUCH_MAX_EVENTS,
                              &count) != 0);
    assert(memcmp(state, &next, sizeof(*state)) == 0);
    assert(count == 0);
}

static void size_tests(int width, int height) {
    struct ta_touch_state state = {0}, next;
    struct ta_touch_event events[TA_TOUCH_MAX_EVENTS];
    size_t count = 0;
    char message[128];
    snprintf(message, sizeof(message), "s 0\nT 7\na 1\nX %d\nY %d\ne 0\nS 0\n", width, height);
    assert(ta_touch_translate(&state, (const uint8_t *)message, strlen(message),
                              width, height, &next, events, TA_TOUCH_MAX_EVENTS, &count) == 0);
    assert(events[2].code == ABS_MT_POSITION_X && events[2].value == width - 1);
    assert(events[3].code == ABS_MT_POSITION_Y && events[3].value == height - 1);
    state = next;
    const char *zero = "s 0\nX 0\nY 0\ne 0\nS 0\n";
    assert(ta_touch_translate(&state, (const uint8_t *)zero, strlen(zero),
                              width, height, &next, events, TA_TOUCH_MAX_EVENTS, &count) == 0);
    assert(events[1].value == 0 && events[2].value == 0);
    state = next;
    snprintf(message, sizeof(message), "s 0\nX %d\nS 0\n", width + 1);
    assert(ta_touch_translate(&state, (const uint8_t *)message, strlen(message),
                              width, height, &next, events, TA_TOUCH_MAX_EVENTS, &count) != 0);
    assert(count == 0 && memcmp(&state, &next, sizeof(state)) == 0);
    assert(ta_touch_release_all(&state, events, TA_TOUCH_MAX_EVENTS, &count) == 0);
    assert(!state.active[0] && events[count - 2].code == BTN_TOUCH && events[count - 2].value == 0);
}

int main(void) {
    struct ta_touch_state state = {0};
    struct ta_touch_event events[TA_TOUCH_MAX_EVENTS];
    size_t count;

    translate(&state, "s 0\nT 42\na 1\nX 1920\nY 1080\ne 0\nS 0\n",
              events, &count);
    assert(count == 6 && state.active[0]);
    assert(events[2].code == ABS_MT_POSITION_X && events[2].value == 1919);
    assert(events[3].code == ABS_MT_POSITION_Y && events[3].value == 1079);
    assert(events[4].code == BTN_TOUCH && events[4].value == 1);
    assert(events[5].type == EV_SYN);

    translate(&state, "s 0\nX 25\nY 36\ne 0\nS 0\n", events, &count);
    assert(count == 5 && state.active[0]);
    assert(events[1].code == ABS_MT_POSITION_X && events[1].value == 25);

    translate(&state, "s 1\nT 43\na 1\nX 100\nY 200\ne 0\nS 0\n",
              events, &count);
    assert(state.active[0] && state.active[1]);
    translate(&state, "s 0\nT -1\na 0\ne 0\nS 0\n", events, &count);
    assert(!state.active[0] && state.active[1]);
    assert(events[count - 2].value == 1);

    invalid(&state, "s 10\nX 1\nS 0\n");
    invalid(&state, "s 1\nX -1\nS 0\n");
    invalid(&state, "s 1\nX 1921\nS 0\n");
    invalid(&state, "s 1\nT 9\n");
    invalid(&state, "s 1\nT 9\nQ 7\nS 0\n");
    invalid(&state, "s 1\nT 9\nS 0");
    assert(state.active[1]);

    assert(ta_touch_release_all(&state, events, TA_TOUCH_MAX_EVENTS, &count) == 0);
    assert(count == 4 && !state.active[1]);
    assert(events[1].code == ABS_MT_TRACKING_ID && events[1].value == -1);
    assert(events[2].code == BTN_TOUCH && events[2].value == 0);
    size_tests(960, 640);
    size_tests(640, 960);
    size_tests(390, 844);
    puts("touch protocol tests passed");
    return 0;
}
