/* SPDX-License-Identifier: GPL-3.0-only */
#include <assert.h>
#define main display_program_main
#include "../../runtime/display/tesla-android-x86-display.c"
#undef main

int main(int argc, char **argv) {
    assert(argc == 7);
    char *invalid[] = {"diagnostic-test", "x", "y"};
    assert(display_program_main(3, invalid) == 2);
    assert(MAX_DISPLAY_CLIENTS == 4);
    const size_t expected[] = {64, 0, 0, 0, MAX_FRAME_SIZE, 0};
    for (unsigned i = 0; i < 6; i++) {
        unsigned char *frame = NULL; size_t size = 0;
        screencap_path = argv[i + 1];
        int result = capture_png(&frame, &size);
        if (expected[i]) {
            assert(result == 0); assert(frame); assert(size == expected[i]);
            assert(frame[0] == 137 && frame[7] == 10 && frame[size - 1] == 0);
            free(frame);
        } else {
            assert(result == -1); assert(frame == NULL); assert(size == 0);
        }
    }
    puts("Synthetic diagnostic capture checks passed (6 cases); no server or Android");
    return 0;
}
