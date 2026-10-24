/* SPDX-License-Identifier: GPL-3.0-only */
#include "touch_keepalive.h"
#include <errno.h>
#include <time.h>
#include <ws.h>

static void *keepalive_loop(void *value) {
    struct ta_touch_keepalive *keepalive = value;
    pthread_mutex_lock(&keepalive->mutex);
    while (!keepalive->stopping) {
        struct timespec deadline;
        if (clock_gettime(CLOCK_MONOTONIC, &deadline) != 0) break;
        deadline.tv_sec += keepalive->interval_ms / 1000;
        deadline.tv_nsec += (long)(keepalive->interval_ms % 1000) * 1000000;
        if (deadline.tv_nsec >= 1000000000) {
            deadline.tv_sec++;
            deadline.tv_nsec -= 1000000000;
        }
        int result = 0;
        while (!keepalive->stopping && result == 0)
            result = pthread_cond_timedwait(&keepalive->condition, &keepalive->mutex, &deadline);
        if (keepalive->stopping || result != ETIMEDOUT) break;
        /* libws owns client lifetimes; never retain an owner pointer across ticks. */
        pthread_mutex_unlock(&keepalive->mutex);
        ws_ping(NULL, 3);
        pthread_mutex_lock(&keepalive->mutex);
    }
    pthread_mutex_unlock(&keepalive->mutex);
    return NULL;
}

int ta_touch_keepalive_start(struct ta_touch_keepalive *keepalive, unsigned interval_ms) {
    if (!keepalive || interval_ms == 0 || interval_ms > 60000) return -1;
    keepalive->interval_ms = interval_ms;
    keepalive->stopping = 0;
    if (pthread_mutex_init(&keepalive->mutex, NULL) != 0) return -1;
    pthread_condattr_t attributes;
    if (pthread_condattr_init(&attributes) != 0) {
        pthread_mutex_destroy(&keepalive->mutex);
        return -1;
    }
    int result = pthread_condattr_setclock(&attributes, CLOCK_MONOTONIC);
    if (result == 0) result = pthread_cond_init(&keepalive->condition, &attributes);
    pthread_condattr_destroy(&attributes);
    if (result != 0) {
        pthread_mutex_destroy(&keepalive->mutex);
        return -1;
    }
    if (pthread_create(&keepalive->thread, NULL, keepalive_loop, keepalive) == 0) return 0;
    pthread_cond_destroy(&keepalive->condition);
    pthread_mutex_destroy(&keepalive->mutex);
    return -1;
}

void ta_touch_keepalive_stop(struct ta_touch_keepalive *keepalive) {
    pthread_mutex_lock(&keepalive->mutex);
    keepalive->stopping = 1;
    pthread_cond_signal(&keepalive->condition);
    pthread_mutex_unlock(&keepalive->mutex);
    pthread_join(keepalive->thread, NULL);
    pthread_cond_destroy(&keepalive->condition);
    pthread_mutex_destroy(&keepalive->mutex);
}
