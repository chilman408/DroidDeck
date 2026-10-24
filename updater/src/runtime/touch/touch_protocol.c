/* SPDX-License-Identifier: GPL-3.0-only */
#include "touch_protocol.h"

#include <errno.h>
#include <limits.h>
#include <linux/input.h>
#include <stdlib.h>
#include <string.h>

static int add_event(struct ta_touch_event *events, size_t capacity,
                     size_t *count, uint16_t type, uint16_t code, int32_t value) {
    if (*count >= capacity) {
        return -1;
    }
    events[*count] = (struct ta_touch_event){type, code, value};
    ++*count;
    return 0;
}

static int parse_number(const uint8_t *text, size_t length, int *value) {
    char buffer[16];
    char *end;
    long parsed;

    if (length == 0 || length >= sizeof(buffer) || memchr(text, 0, length)) {
        return -1;
    }
    memcpy(buffer, text, length);
    buffer[length] = 0;
    errno = 0;
    parsed = strtol(buffer, &end, 10);
    if (errno || end == buffer || *end || parsed < INT_MIN || parsed > INT_MAX) {
        return -1;
    }
    *value = (int)parsed;
    return 0;
}

static int any_active(const struct ta_touch_state *state) {
    for (size_t slot = 0; slot < TA_TOUCH_SLOTS; ++slot) {
        if (state->active[slot]) {
            return 1;
        }
    }
    return 0;
}

int ta_touch_translate(const struct ta_touch_state *state,
                       const uint8_t *message, size_t message_size,
                       int width, int height,
                       struct ta_touch_state *next,
                       struct ta_touch_event *events, size_t capacity,
                       size_t *event_count) {
    struct ta_touch_state working;
    size_t used = 0;
    size_t position = 0;
    int selected_slot = -1;
    int ended_with_sync = 0;

    if (!state || !message || !next || !events || !event_count ||
        message_size == 0 || message_size > TA_TOUCH_MAX_MESSAGE ||
        width < 1 || width > 8192 || height < 1 || height > 8192) {
        return -1;
    }
    *event_count = 0;
    *next = *state;
    working = *state;

    while (position < message_size) {
        const uint8_t *line = message + position;
        const uint8_t *newline = memchr(line, '\n', message_size - position);
        size_t length;
        int value;

        if (!newline) {
            return -1;
        }
        length = (size_t)(newline - line);
        if (length < 3 || line[1] != ' ' ||
            parse_number(line + 2, length - 2, &value) != 0) {
            return -1;
        }
        ended_with_sync = 0;

        switch (line[0]) {
        case 's':
            if (value < 0 || value >= TA_TOUCH_SLOTS ||
                add_event(events, capacity, &used, EV_ABS, ABS_MT_SLOT, value)) {
                return -1;
            }
            selected_slot = value;
            break;
        case 'T':
            if (selected_slot < 0 || value < -1 ||
                add_event(events, capacity, &used, EV_ABS,
                          ABS_MT_TRACKING_ID, value)) {
                return -1;
            }
            working.active[selected_slot] = value != -1;
            break;
        case 'X':
        case 'Y': {
            int limit = line[0] == 'X' ? width : height;
            uint16_t code = line[0] == 'X' ? ABS_MT_POSITION_X : ABS_MT_POSITION_Y;
            if (selected_slot < 0 || !working.active[selected_slot] ||
                value < 0 || value > limit ||
                add_event(events, capacity, &used, EV_ABS, code,
                          value == limit ? limit - 1 : value)) {
                return -1;
            }
            break;
        }
        case 'a':
            if (selected_slot < 0 || (value != 0 && value != 1)) {
                return -1;
            }
            break;
        case 'e':
            if (value != 0) {
                return -1;
            }
            break;
        case 'S':
            if (value != 0 ||
                add_event(events, capacity, &used, EV_KEY, BTN_TOUCH,
                          any_active(&working)) ||
                add_event(events, capacity, &used, EV_SYN, SYN_REPORT, 0)) {
                return -1;
            }
            selected_slot = -1;
            ended_with_sync = 1;
            break;
        default:
            return -1;
        }
        position = (size_t)(newline - message) + 1;
    }

    if (!ended_with_sync) {
        return -1;
    }
    *next = working;
    *event_count = used;
    return 0;
}

int ta_touch_release_all(struct ta_touch_state *state,
                         struct ta_touch_event *events, size_t capacity,
                         size_t *event_count) {
    size_t needed = 2;
    size_t used = 0;

    if (!state || !events || !event_count) {
        return -1;
    }
    *event_count = 0;
    for (size_t slot = 0; slot < TA_TOUCH_SLOTS; ++slot) {
        if (state->active[slot]) {
            needed += 2;
        }
    }
    if (capacity < needed) {
        return -1;
    }

    for (size_t slot = 0; slot < TA_TOUCH_SLOTS; ++slot) {
        if (state->active[slot]) {
            add_event(events, capacity, &used, EV_ABS, ABS_MT_SLOT, (int)slot);
            add_event(events, capacity, &used, EV_ABS, ABS_MT_TRACKING_ID, -1);
        }
    }
    add_event(events, capacity, &used, EV_KEY, BTN_TOUCH, 0);
    add_event(events, capacity, &used, EV_SYN, SYN_REPORT, 0);
    memset(state, 0, sizeof(*state));
    *event_count = used;
    return 0;
}
