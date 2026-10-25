/* SPDX-License-Identifier: GPL-3.0-only */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <gbm.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>
#include <wayland-client.h>
#include "xdg-shell-client-protocol.h"
#include "linux-dmabuf-client-protocol.h"
#include "compositor-cpu-buffer-pattern.h"

enum { WIDTH = 320, HEIGHT = 240, SLOTS = 3 };
struct slot {
    struct wl_buffer *buffer;
    struct gbm_bo *bo;
    void *shm;
    bool busy;
};
struct app {
    struct wl_display *display;
    struct wl_registry *registry;
    struct wl_compositor *compositor;
    struct wl_shm *shm;
    struct xdg_wm_base *wm;
    struct zwp_linux_dmabuf_v1 *dmabuf;
    struct wl_surface *surface;
    struct xdg_surface *xdg;
    struct xdg_toplevel *top;
    struct wl_callback *frame;
    struct gbm_device *gbm;
    struct slot slots[SLOTS];
    int drm;
    bool dma, configured, ready, closed;
    unsigned submitted;
};
static int fail(const char *stage) { fprintf(stderr, "CPU buffer test failed: %s\n", stage); return -1; }
static uint64_t clock_ms(void) {
    struct timespec value;
    if (clock_gettime(CLOCK_MONOTONIC, &value)) return 0;
    return (uint64_t)value.tv_sec * 1000 + (uint64_t)value.tv_nsec / 1000000;
}
static void release(void *data, struct wl_buffer *buffer) {
    (void)buffer; ((struct slot *)data)->busy = false;
}
static const struct wl_buffer_listener buffer_listener = { .release = release };
static void frame_done(void *data, struct wl_callback *callback, uint32_t time) {
    (void)time; struct app *app = data;
    wl_callback_destroy(callback); app->frame = NULL; app->ready = true;
}
static const struct wl_callback_listener frame_listener = { .done = frame_done };
static void ping(void *data, struct xdg_wm_base *wm, uint32_t serial) {
    (void)data; xdg_wm_base_pong(wm, serial);
}
static const struct xdg_wm_base_listener wm_listener = { .ping = ping };
static void configure(void *data, struct xdg_surface *surface, uint32_t serial) {
    struct app *app = data;
    xdg_surface_ack_configure(surface, serial); app->configured = true;
}
static const struct xdg_surface_listener xdg_listener = { .configure = configure };
static void top_configure(void *data, struct xdg_toplevel *top, int32_t w, int32_t h, struct wl_array *states) {
    (void)data; (void)top; (void)w; (void)h; (void)states;
}
static void close_top(void *data, struct xdg_toplevel *top) {
    (void)top; ((struct app *)data)->closed = true;
}
static const struct xdg_toplevel_listener top_listener = { .configure = top_configure, .close = close_top };
static void dma_format(void *data, struct zwp_linux_dmabuf_v1 *dma, uint32_t format) {
    (void)data; (void)dma; (void)format;
}
static void dma_modifier(void *data, struct zwp_linux_dmabuf_v1 *dma, uint32_t format, uint32_t hi, uint32_t lo) {
    (void)data; (void)dma; (void)format; (void)hi; (void)lo;
}
static const struct zwp_linux_dmabuf_v1_listener dma_listener = { .format = dma_format, .modifier = dma_modifier };
static void global(void *data, struct wl_registry *registry, uint32_t name, const char *interface, uint32_t version) {
    struct app *app = data;
    if (!strcmp(interface, "wl_compositor") && !app->compositor && version >= 4)
        app->compositor = wl_registry_bind(registry, name, &wl_compositor_interface, 4);
    else if (!strcmp(interface, "wl_shm") && !app->shm)
        app->shm = wl_registry_bind(registry, name, &wl_shm_interface, 1);
    else if (!strcmp(interface, "xdg_wm_base") && !app->wm) {
        app->wm = wl_registry_bind(registry, name, &xdg_wm_base_interface, 1);
        xdg_wm_base_add_listener(app->wm, &wm_listener, app);
    } else if (!strcmp(interface, "zwp_linux_dmabuf_v1") && !app->dmabuf && version >= 3) {
        app->dmabuf = wl_registry_bind(registry, name, &zwp_linux_dmabuf_v1_interface, 3);
        zwp_linux_dmabuf_v1_add_listener(app->dmabuf, &dma_listener, app);
    }
}
static void global_remove(void *data, struct wl_registry *registry, uint32_t name) {
    (void)data; (void)registry; (void)name;
}
static const struct wl_registry_listener registry_listener = { .global = global, .global_remove = global_remove };

/* Use prepare/cancel-read so idle or missing frame callbacks cannot hang shutdown. */
static int pump(struct app *app) {
    while (wl_display_prepare_read(app->display) != 0)
        if (wl_display_dispatch_pending(app->display) < 0) return fail("dispatch");
    int flushed = wl_display_flush(app->display);
    if (flushed < 0 && errno != EAGAIN) { wl_display_cancel_read(app->display); return fail("flush"); }
    struct pollfd fd = { .fd = wl_display_get_fd(app->display),
                         .events = POLLIN | (flushed < 0 ? POLLOUT : 0) };
    int result = poll(&fd, 1, 100);
    if (result < 0 || (fd.revents & (POLLERR | POLLHUP | POLLNVAL))) {
        wl_display_cancel_read(app->display); return fail("poll");
    }
    if (fd.revents & POLLIN) {
        if (wl_display_read_events(app->display) < 0) return fail("read");
    } else wl_display_cancel_read(app->display);
    return wl_display_dispatch_pending(app->display) < 0 ? fail("pending") : 0;
}
static int make_slot(struct app *app, struct slot *slot) {
    if (app->dma) {
        slot->bo = gbm_bo_create(app->gbm, WIDTH, HEIGHT, GBM_FORMAT_XBGR8888,
                                 GBM_BO_USE_RENDERING | GBM_BO_USE_LINEAR);
        if (!slot->bo || gbm_bo_get_plane_count(slot->bo) != 1) return fail("linear-allocation");
        uint64_t modifier = gbm_bo_get_modifier(slot->bo);
        if (modifier != 0 && modifier != UINT64_C(0x00ffffffffffffff)) return fail("nonlinear-modifier");
        int fd = gbm_bo_get_fd(slot->bo);
        if (fd < 0) return fail("export");
        struct zwp_linux_buffer_params_v1 *params = zwp_linux_dmabuf_v1_create_params(app->dmabuf);
        zwp_linux_buffer_params_v1_add(params, fd, 0, gbm_bo_get_offset(slot->bo, 0),
            gbm_bo_get_stride_for_plane(slot->bo, 0), modifier >> 32, modifier & 0xffffffff);
        slot->buffer = zwp_linux_buffer_params_v1_create_immed(params, WIDTH, HEIGHT, GBM_FORMAT_XBGR8888, 0);
        zwp_linux_buffer_params_v1_destroy(params); close(fd);
    } else {
        const size_t size = WIDTH * HEIGHT * 4;
        int fd = memfd_create("generated-cpu-pattern", MFD_CLOEXEC);
        if (fd < 0) return fail("memfd");
        if (ftruncate(fd, size)) { close(fd); return fail("truncate"); }
        slot->shm = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        if (slot->shm == MAP_FAILED) { slot->shm = NULL; close(fd); return fail("mmap"); }
        struct wl_shm_pool *pool = wl_shm_create_pool(app->shm, fd, size);
        slot->buffer = wl_shm_pool_create_buffer(pool, 0, WIDTH, HEIGHT, WIDTH * 4, WL_SHM_FORMAT_ABGR8888);
        wl_shm_pool_destroy(pool); close(fd);
    }
    if (!slot->buffer) return fail("buffer");
    wl_buffer_add_listener(slot->buffer, &buffer_listener, slot);
    return 0;
}
static int submit(struct app *app, uint64_t elapsed) {
    if (!app->configured || !app->ready) return 0;
    struct slot *slot = NULL;
    for (unsigned i = 0; i < SLOTS; i++) if (!app->slots[i].busy) { slot = &app->slots[i]; break; }
    if (!slot) return 0;
    unsigned phase = (elapsed / 250) % 4;
    if (app->dma) {
        uint32_t stride = 0; void *handle = NULL;
        void *map = gbm_bo_map(slot->bo, 0, 0, WIDTH, HEIGHT, GBM_BO_TRANSFER_WRITE, &stride, &handle);
        if (!map) return fail("cpu-map");
        int result = paint_cpu_pattern(map, WIDTH, HEIGHT, stride, phase);
        gbm_bo_unmap(slot->bo, handle);
        if (result) return fail("cpu-paint");
    } else if (paint_cpu_pattern(slot->shm, WIDTH, HEIGHT, WIDTH * 4, phase)) return fail("shm-paint");
    app->frame = wl_surface_frame(app->surface);
    wl_callback_add_listener(app->frame, &frame_listener, app);
    wl_surface_attach(app->surface, slot->buffer, 0, 0);
    wl_surface_damage_buffer(app->surface, 0, 0, WIDTH, HEIGHT);
    slot->busy = true; app->ready = false;
    wl_surface_commit(app->surface); app->submitted++;
    return 0;
}
static void cleanup(struct app *app) {
    if (app->frame) wl_callback_destroy(app->frame);
    if (app->top) xdg_toplevel_destroy(app->top);
    if (app->xdg) xdg_surface_destroy(app->xdg);
    if (app->surface) wl_surface_destroy(app->surface);
    for (unsigned i = 0; i < SLOTS; i++) {
        if (app->slots[i].buffer) wl_buffer_destroy(app->slots[i].buffer);
        if (app->slots[i].bo) gbm_bo_destroy(app->slots[i].bo);
        if (app->slots[i].shm) munmap(app->slots[i].shm, WIDTH * HEIGHT * 4);
    }
    if (app->dmabuf) zwp_linux_dmabuf_v1_destroy(app->dmabuf);
    if (app->wm) xdg_wm_base_destroy(app->wm);
    if (app->shm) wl_shm_destroy(app->shm);
    if (app->compositor) wl_compositor_destroy(app->compositor);
    if (app->registry) wl_registry_destroy(app->registry);
    if (app->display) wl_display_disconnect(app->display);
    if (app->gbm) gbm_device_destroy(app->gbm);
    if (app->drm >= 0) close(app->drm);
}
int main(int argc, char **argv) {
    const char *socket = getenv("WAYLAND_DISPLAY");
    if (getuid() != 1000 || argc != 2 || (strcmp(argv[1], "--shm") && strcmp(argv[1], "--dmabuf")) ||
        !socket || strcmp(socket, "wayland-capture-cpu-buffer-20261011n")) return fail("fixed-profile") != 0;
    struct app app = { .drm = -1, .dma = !strcmp(argv[1], "--dmabuf"), .ready = true };
    int status = 1;
    uint64_t started = clock_ms();
    app.display = wl_display_connect(socket);
    if (!app.display) { fail("connect"); goto done; }
    app.registry = wl_display_get_registry(app.display);
    wl_registry_add_listener(app.registry, &registry_listener, &app);
    while ((!app.compositor || !app.wm || !app.shm || (app.dma && !app.dmabuf)) && clock_ms() - started < 3000)
        if (pump(&app)) goto done;
    if (!app.compositor || !app.wm || !app.shm || (app.dma && !app.dmabuf)) { fail("globals"); goto done; }
    if (app.dma) {
        app.drm = open("/dev/dri/renderD128", O_RDWR | O_CLOEXEC);
        if (app.drm < 0 || !(app.gbm = gbm_create_device(app.drm))) { fail("gbm-device"); goto done; }
    }
    app.surface = wl_compositor_create_surface(app.compositor);
    app.xdg = xdg_wm_base_get_xdg_surface(app.wm, app.surface);
    xdg_surface_add_listener(app.xdg, &xdg_listener, &app);
    app.top = xdg_surface_get_toplevel(app.xdg);
    xdg_toplevel_add_listener(app.top, &top_listener, &app);
    xdg_toplevel_set_title(app.top, "Generated CPU buffer fixture");
    wl_surface_commit(app.surface);
    for (unsigned i = 0; i < SLOTS; i++) if (make_slot(&app, &app.slots[i])) goto done;
    while (!app.closed && clock_ms() - started < 25000) {
        if (submit(&app, clock_ms() - started) || pump(&app)) goto done;
    }
    if (app.submitted < 60) { fail("insufficient-submissions"); goto done; }
    printf("{\"submittedFrames\":%u,\"cpuWritten\":true,\"dmabuf\":%s,\"width\":320,\"height\":240}\n",
        app.submitted, app.dma ? "true" : "false");
    status = 0;
done:
    cleanup(&app);
    return status;
}
