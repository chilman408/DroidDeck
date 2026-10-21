/* SPDX-License-Identifier: GPL-3.0-only */
#include <errno.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>
#ifndef FIXTURE_MODE
#define FIXTURE_MODE 0
#endif

static int emit(const unsigned char *bytes, size_t size) {
    while (size) {
        ssize_t count = write(STDOUT_FILENO, bytes, size);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) return -1;
        bytes += count; size -= (size_t)count;
    }
    return 0;
}
int main(int argc, char **argv) {
    unsigned char signature[] = {137, 80, 78, 71, 13, 10, 26, 10};
    static const unsigned char zeros[4095] = {0};
    if (argc != 2 || strcmp(argv[1], "-p")) return 2;
    if (FIXTURE_MODE == 1) signature[0] = 0;
    size_t size = FIXTURE_MODE == 3 ? 4 : FIXTURE_MODE == 4 ? 16U * 1024U * 1024U :
        FIXTURE_MODE == 5 ? 16U * 1024U * 1024U + 1 : 64;
    size_t prefix = size < sizeof(signature) ? size : sizeof(signature);
    if (emit(signature, prefix)) return 3;
    size -= prefix;
    while (size) {
        size_t count = size < sizeof(zeros) ? size : sizeof(zeros);
        if (emit(zeros, count)) return 3;
        size -= count;
    }
    return FIXTURE_MODE == 2 ? 3 : 0;
}
