/* SPDX-License-Identifier: GPL-3.0-only */
#include <assert.h>
#include <stdlib.h>
#include <string.h>
#include "../runtime/waydroid-display/compositor-cpu-buffer-pattern.h"
int main(void) {
    const size_t stride = 1296, size = stride * 240;
    unsigned char *data = malloc(size);
    assert(data);
    memset(data, 0x5a, size);
    for (unsigned phase = 0; phase < 4; phase++) {
        assert(paint_cpu_pattern(data, 320, 240, stride, phase) == 0);
        assert(data[3] == 255);
        assert(memcmp(data, data + 160 * 4, 3) != 0);
        for (unsigned y = 0; y < 240; y++)
            for (size_t x = 1280; x < stride; x++) assert(data[y * stride + x] == 0x5a);
    }
    assert(paint_cpu_pattern(data, 320, 240, stride, 0) == 0);
    assert(data[0] == 220 && data[1] == 30 && data[2] == 30);
    assert(data[640] == 30 && data[641] == 220 && data[642] == 220);
    assert(paint_cpu_pattern(NULL, 320, 240, stride, 0) == -1);
    assert(paint_cpu_pattern(data, 319, 240, stride, 0) == -1);
    assert(paint_cpu_pattern(data, 320, 239, stride, 0) == -1);
    assert(paint_cpu_pattern(data, 320, 240, 1279, 0) == -1);
    assert(paint_cpu_pattern(data, 320, 240, 16385, 0) == -1);
    assert(paint_cpu_pattern(data, 320, 240, stride, 4) == -1);
    free(data);
    return 0;
}
