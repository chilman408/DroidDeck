/* SPDX-License-Identifier: GPL-3.0-only */
#include "touch_protocol.h"
#include "touch_mapping.h"
#include "touch_keepalive.h"

#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/input.h>
#include <linux/uinput.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>
#include <ws.h>

static pthread_mutex_t touch_lock = PTHREAD_MUTEX_INITIALIZER;
static struct ta_touch_state touch_state;
static ws_cli_conn_t *owner;
static int device_fd = -1;
static int screen_width;
static int screen_height;
static struct ta_touch_mapping mapping;

static int emit_events(const struct ta_touch_event *events, size_t count) {
    for (size_t i = 0; i < count; ++i) {
        struct input_event event = {0};
        event.type = events[i].type;
        event.code = events[i].code;
        event.value = events[i].value;
        // Android rejects off-frame raw DOWN events before its affine calibration.
        if (event.type == EV_ABS && event.code == ABS_MT_POSITION_X)
            event.value = ta_touch_map_axis(&mapping, 0, event.value);
        if (event.type == EV_ABS && event.code == ABS_MT_POSITION_Y)
            event.value = ta_touch_map_axis(&mapping, 1, event.value);
        if (write(device_fd, &event, sizeof(event)) != sizeof(event)) {
            return -1;
        }
    }
    return 0;
}

static void release_touches(void) {
    struct ta_touch_state next = touch_state;
    struct ta_touch_event events[TA_TOUCH_MAX_EVENTS];
    size_t count;
    if (ta_touch_release_all(&next, events, TA_TOUCH_MAX_EVENTS, &count) == 0 &&
        emit_events(events, count) == 0) {
        touch_state = next;
    }
}

static void on_open(ws_cli_conn_t *client) {
    int reject;
    pthread_mutex_lock(&touch_lock);
    reject = owner != NULL;
    if (!reject) {
        owner = client;
    }
    pthread_mutex_unlock(&touch_lock);
    if (reject) {
        ws_close_client(client);
    }
}

static void on_close(ws_cli_conn_t *client) {
    pthread_mutex_lock(&touch_lock);
    if (owner == client) {
        release_touches();
        owner = NULL;
    }
    pthread_mutex_unlock(&touch_lock);
}

static void on_message(ws_cli_conn_t *client, const unsigned char *message,
                       uint64_t size, int type) {
    struct ta_touch_state next;
    struct ta_touch_event events[TA_TOUCH_MAX_EVENTS];
    size_t count;
    int invalid = 0;

    pthread_mutex_lock(&touch_lock);
    if (owner == client) {
        if (type != WS_FR_OP_TXT || size > TA_TOUCH_MAX_MESSAGE ||
            ta_touch_translate(&touch_state, message, (size_t)size,
                               screen_width, screen_height, &next, events,
                               TA_TOUCH_MAX_EVENTS, &count) != 0 ||
            emit_events(events, count) != 0) {
            release_touches();
            invalid = 1;
        } else {
            touch_state = next;
        }
    }
    pthread_mutex_unlock(&touch_lock);
    if (invalid) {
        ws_close_client(client);
    }
}

static int configure_axis(int fd, int axis, int minimum, int maximum) {
    struct uinput_abs_setup setup = {0};
    setup.code = axis;
    setup.absinfo.minimum = minimum;
    setup.absinfo.maximum = maximum;
    return ioctl(fd, UI_ABS_SETUP, &setup);
}

static int create_device(int width, int height) {
    struct uinput_setup setup = {0};
    int fd = open("/dev/uinput", O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        perror("open /dev/uinput");
        return -1;
    }
    if (ioctl(fd, UI_SET_EVBIT, EV_SYN) < 0 ||
        ioctl(fd, UI_SET_EVBIT, EV_KEY) < 0 ||
        ioctl(fd, UI_SET_EVBIT, EV_ABS) < 0 ||
        ioctl(fd, UI_SET_KEYBIT, BTN_TOUCH) < 0 ||
        ioctl(fd, UI_SET_PROPBIT, INPUT_PROP_DIRECT) < 0 ||
        ioctl(fd, UI_SET_ABSBIT, ABS_MT_SLOT) < 0 ||
        ioctl(fd, UI_SET_ABSBIT, ABS_MT_TRACKING_ID) < 0 ||
        ioctl(fd, UI_SET_ABSBIT, ABS_MT_POSITION_X) < 0 ||
        ioctl(fd, UI_SET_ABSBIT, ABS_MT_POSITION_Y) < 0 ||
        configure_axis(fd, ABS_MT_SLOT, 0, TA_TOUCH_SLOTS - 1) < 0 ||
        configure_axis(fd, ABS_MT_TRACKING_ID, 0, INT_MAX) < 0 ||
        configure_axis(fd, ABS_MT_POSITION_X, 0, width - 1) < 0 ||
        configure_axis(fd, ABS_MT_POSITION_Y, 0, height - 1) < 0) {
        perror("configure uinput");
        close(fd);
        return -1;
    }
    setup.id.bustype = BUS_VIRTUAL;
    setup.id.vendor = 0x5441;
    setup.id.product = 0x0001;
    snprintf(setup.name, sizeof(setup.name), "TeslaAndroid x86 Touchscreen");
    if (ioctl(fd, UI_DEV_SETUP, &setup) < 0 ||
        ioctl(fd, UI_DEV_CREATE) < 0) {
        perror("create uinput device");
        close(fd);
        return -1;
    }
    return fd;
}

static int parse_dimension(const char *text, int *value) {
    char *end;
    long parsed;
    errno = 0;
    parsed = strtol(text, &end, 10);
    if (errno || end == text || *end || parsed < 1 || parsed > 8192) {
        return -1;
    }
    *value = (int)parsed;
    return 0;
}

int main(int argc, char **argv) {
    struct ws_events callbacks = {
        .onopen = on_open,
        .onclose = on_close,
        .onmessage = on_message,
    };
    if ((argc != 3 && argc != 9) ||
        parse_dimension(argv[1], &screen_width) != 0 ||
        parse_dimension(argv[2], &screen_height) != 0) {
        fprintf(stderr, "usage: %s WIDTH HEIGHT [DEVICE_WIDTH DEVICE_HEIGHT LEFT TOP RIGHT BOTTOM]\n", argv[0]);
        return 2;
    }
    mapping = (struct ta_touch_mapping){screen_width, screen_height, screen_width,
                                       screen_height, 0, 0, screen_width, screen_height};
    if (argc == 9) {
        int *values[] = {&mapping.device_width, &mapping.device_height, &mapping.left,
                         &mapping.top, &mapping.right, &mapping.bottom};
        for (int i = 0; i < 6; i++) {
            char *end;
            errno = 0;
            long value = strtol(argv[i + 3], &end, 10);
            if (errno || end == argv[i + 3] || *end || value < 0 || value > 8192) return 2;
            *values[i] = (int)value;
        }
    }
    if (!ta_touch_mapping_valid(&mapping)) return 2;
    device_fd = create_device(screen_width, screen_height);
    if (device_fd < 0) {
        return 1;
    }
    fprintf(stderr, "Touchscreen %dx%d; WebSocket port 9999\n",
            screen_width, screen_height);
    struct ta_touch_keepalive keepalive;
    if (ta_touch_keepalive_start(&keepalive, 15000) != 0) {
        fprintf(stderr, "Touchscreen keepalive unavailable\n");
        ioctl(device_fd, UI_DEV_DESTROY);
        close(device_fd);
        return 1;
    }
    int result = ws_socket(&callbacks, 9999, 0, 1000);
    ta_touch_keepalive_stop(&keepalive);
    ioctl(device_fd, UI_DEV_DESTROY);
    close(device_fd);
    return result == 0 ? 0 : 1;
}
