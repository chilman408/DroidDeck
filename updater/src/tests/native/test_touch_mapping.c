/* SPDX-License-Identifier: GPL-3.0-only */
#include "../../runtime/touch/touch_mapping.h"
#include <assert.h>
#include <stdio.h>

static void check(int width, int height, int device_width, int device_height,
                  int left, int top, int right, int bottom) {
    struct ta_touch_mapping m = {width, height, device_width, device_height,
                                left, top, right, bottom};
    assert(ta_touch_mapping_valid(&m));
    for (int axis = 0; axis < 2; axis++) {
        int logical = axis ? height : width;
        int device = axis ? device_height : device_width;
        int start = axis ? top : left, end = axis ? bottom : right;
        int previous = -1;
        for (int x = 0; x < logical; x++) {
            int raw = ta_touch_map_axis(&m, axis, x);
            double physical = (double)raw * device / logical;
            double received = (physical - start) * logical / (end - start);
            assert(raw >= previous && raw >= 0 && raw < logical);
            // Android's pre-calibration rejection must accept every DOWN.
            assert(physical >= start && physical < end);
            assert(received - x > -2.1 && received - x < 2.1);
            previous = raw;
        }
    }
}
int main(void) {
    check(976, 720, 1280, 768, 119, 0, 1160, 768);
    struct ta_touch_mapping live = {976, 720, 1280, 768, 119, 0, 1160, 768};
    int live_x[] = {3, 488, 972};
    for (unsigned i = 0; i < sizeof(live_x) / sizeof(live_x[0]); i++) {
        double physical = (double)ta_touch_map_axis(&live, 0, live_x[i]) * 1280 / 976;
        double error = (physical - 119) * 976 / (1160 - 119) - live_x[i];
        assert(error > -1 && error < 1);
    }
    check(960, 640, 1280, 768, 64, 0, 1216, 768);
    check(640, 960, 1280, 768, 384, 0, 896, 768);
    check(1920, 720, 1280, 768, 0, 144, 1280, 624);
    check(1280, 768, 1280, 768, 0, 0, 1280, 768);
    struct ta_touch_mapping invalid = {976, 720, 1280, 768, 119, 0, 119, 768};
    assert(!ta_touch_mapping_valid(&invalid));
    invalid.right = 1281;
    assert(!ta_touch_mapping_valid(&invalid));
    puts("touch mapping tests passed");
    return 0;
}
