/* SPDX-License-Identifier: GPL-3.0-only */
#include <dlfcn.h>
#include <stdio.h>

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    const char *utility_symbols[] = {"gutil_log_get_type", "gutil_idle_pool_new"};
    const char *binder_symbols[] = {"gbinder_servicemanager_new2", "gbinder_client_new",
        "gbinder_client_new_request", "gbinder_remote_reply_unref"};
    void *utility = dlopen(argv[1], RTLD_NOW | RTLD_LOCAL);
    if (!utility) return 3;
    void *binder = dlopen(argv[2], RTLD_NOW | RTLD_LOCAL);
    if (!binder) { dlclose(utility); return 4; }
    int failed = 0;
    for (unsigned i = 0; i < sizeof(utility_symbols) / sizeof(utility_symbols[0]); i++)
        if (!dlsym(utility, utility_symbols[i])) failed = 1;
    for (unsigned i = 0; i < sizeof(binder_symbols) / sizeof(binder_symbols[0]); i++)
        if (!dlsym(binder, binder_symbols[i])) failed = 1;
    dlclose(binder); dlclose(utility);
    if (failed) return 5;
    puts("Private library symbol checks passed; no Binder functions called");
    return 0;
}
