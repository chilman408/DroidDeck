/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef TESLA_CPU_BUFFER_PATTERN_H
#define TESLA_CPU_BUFFER_PATTERN_H
#include <stddef.h>
#include <stdint.h>

/* Little-endian XBGR8888 / ABGR8888 bytes. Padding is never written. */
static int paint_cpu_pattern(void *pixels, unsigned width, unsigned height,
                             size_t stride, unsigned phase) {
    static const uint8_t palette[4][3] = {
        {220, 30, 30}, {30, 220, 220}, {30, 220, 30}, {220, 30, 220}
    };
    if (!pixels || width != 320 || height != 240 || stride < width * 4 ||
        stride > 16384 || phase > 3) return -1;
    for (unsigned y = 0; y < height; y++) {
        uint8_t *row = (uint8_t *)pixels + y * stride;
        for (unsigned x = 0; x < width; x++) {
            const uint8_t *color = palette[(phase + (x >= width / 2)) % 4];
            row[x * 4] = color[0]; row[x * 4 + 1] = color[1];
            row[x * 4 + 2] = color[2]; row[x * 4 + 3] = 255;
        }
    }
    return 0;
}
#endif
