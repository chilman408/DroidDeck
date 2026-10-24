/* SPDX-License-Identifier: GPL-3.0-only */
#include "touch_mapping.h"
#include <stdint.h>

static int ceil_div(int64_t value, int divisor) {
    return (int)((value + divisor - 1) / divisor);
}
int ta_touch_mapping_valid(const struct ta_touch_mapping *m) {
    if (!m || m->width < 1 || m->width > 8192 || m->height < 1 || m->height > 8192 ||
        m->device_width < 1 || m->device_width > 8192 ||
        m->device_height < 1 || m->device_height > 8192 ||
        m->left < 0 || m->top < 0 || m->right <= m->left || m->bottom <= m->top ||
        m->right > m->device_width || m->bottom > m->device_height) return 0;
    return ceil_div((int64_t)m->left * m->width, m->device_width) <
               ceil_div((int64_t)m->right * m->width, m->device_width) &&
           ceil_div((int64_t)m->top * m->height, m->device_height) <
               ceil_div((int64_t)m->bottom * m->height, m->device_height);
}
int ta_touch_map_axis(const struct ta_touch_mapping *m, int axis, int value) {
    int logical = axis ? m->height : m->width;
    int device = axis ? m->device_height : m->device_width;
    int start = axis ? m->top : m->left, end = axis ? m->bottom : m->right;
    int minimum = ceil_div((int64_t)start * logical, device);
    int maximum = ceil_div((int64_t)end * logical, device) - 1;
    int64_t numerator = (int64_t)start * logical + (int64_t)value * (end - start);
    int mapped = (int)((numerator + device / 2) / device);
    if (mapped < minimum) mapped = minimum;
    if (mapped > maximum) mapped = maximum;
    return mapped;
}
