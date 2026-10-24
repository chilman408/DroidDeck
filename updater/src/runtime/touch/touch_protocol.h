/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef TESLA_ANDROID_X86_TOUCH_PROTOCOL_H
#define TESLA_ANDROID_X86_TOUCH_PROTOCOL_H

#include <stddef.h>
#include <stdint.h>

#define TA_TOUCH_SLOTS 10
#define TA_TOUCH_MAX_EVENTS 128
#define TA_TOUCH_MAX_MESSAGE 4096

struct ta_touch_state {
    uint8_t active[TA_TOUCH_SLOTS];
};

struct ta_touch_event {
    uint16_t type;
    uint16_t code;
    int32_t value;
};

int ta_touch_translate(const struct ta_touch_state *state,
                       const uint8_t *message, size_t message_size,
                       int width, int height,
                       struct ta_touch_state *next,
                       struct ta_touch_event *events, size_t capacity,
                       size_t *event_count);

int ta_touch_release_all(struct ta_touch_state *state,
                         struct ta_touch_event *events, size_t capacity,
                         size_t *event_count);

#endif
