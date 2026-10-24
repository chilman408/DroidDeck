/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef TESLA_ANDROID_X86_TOUCH_MAPPING_H
#define TESLA_ANDROID_X86_TOUCH_MAPPING_H
struct ta_touch_mapping {
    int width, height, device_width, device_height;
    int left, top, right, bottom;
};
int ta_touch_mapping_valid(const struct ta_touch_mapping *mapping);
int ta_touch_map_axis(const struct ta_touch_mapping *mapping, int axis, int value);
#endif
