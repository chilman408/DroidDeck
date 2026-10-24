/* SPDX-License-Identifier: GPL-3.0-only */
#ifndef TA_TOUCH_KEEPALIVE_H
#define TA_TOUCH_KEEPALIVE_H
#include <pthread.h>
struct ta_touch_keepalive {
    pthread_t thread;
    pthread_mutex_t mutex;
    pthread_cond_t condition;
    unsigned interval_ms;
    int stopping;
};
int ta_touch_keepalive_start(struct ta_touch_keepalive *keepalive, unsigned interval_ms);
void ta_touch_keepalive_stop(struct ta_touch_keepalive *keepalive);
#endif
