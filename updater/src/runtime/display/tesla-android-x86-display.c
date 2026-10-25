/* SPDX-License-Identifier: GPL-3.0-only */
#include <errno.h>
#include <pthread.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include <ws.h>

#define MAX_FRAME_SIZE (16U * 1024U * 1024U)
#define MAX_DISPLAY_CLIENTS 4

struct display_stream {
    ws_cli_conn_t *client;
    pthread_t thread;
    int stopping;
};

static pthread_mutex_t streams_lock = PTHREAD_MUTEX_INITIALIZER;
static struct display_stream streams[MAX_DISPLAY_CLIENTS];
static const char *screencap_path = "/system/bin/screencap";

static int capture_png(unsigned char **frame, size_t *size) {
    static const unsigned char signature[] = {137, 80, 78, 71, 13, 10, 26, 10};
    int pipefd[2];
    pid_t child;
    size_t capacity = 256U * 1024U;
    size_t used = 0;
    unsigned char *buffer;
    int status;

    if (pipe(pipefd) < 0) {
        return -1;
    }
    child = fork();
    if (child < 0) {
        close(pipefd[0]);
        close(pipefd[1]);
        return -1;
    }
    if (child == 0) {
        close(pipefd[0]);
        if (dup2(pipefd[1], STDOUT_FILENO) < 0) {
            _exit(127);
        }
        close(pipefd[1]);
        execl(screencap_path, screencap_path, "-p", (char *)NULL);
        _exit(127);
    }
    close(pipefd[1]);
    buffer = malloc(capacity);
    if (!buffer) {
        close(pipefd[0]);
        waitpid(child, &status, 0);
        return -1;
    }
    for (;;) {
        ssize_t count;
        if (used == capacity) {
            if (capacity >= MAX_FRAME_SIZE) {
                unsigned char extra;
                do {
                    count = read(pipefd[0], &extra, 1);
                } while (count < 0 && errno == EINTR);
                if (count != 0) {
                    used = 0;
                }
                break;
            }
            size_t next_capacity = capacity * 2;
            unsigned char *grown = realloc(buffer, next_capacity);
            if (!grown) {
                break;
            }
            capacity = next_capacity;
            buffer = grown;
        }
        count = read(pipefd[0], buffer + used, capacity - used);
        if (count < 0 && errno == EINTR) {
            continue;
        }
        if (count <= 0) {
            if (count < 0) {
                used = 0;
            }
            break;
        }
        used += (size_t)count;
    }
    close(pipefd[0]);
    if (waitpid(child, &status, 0) != child ||
        !WIFEXITED(status) || WEXITSTATUS(status) != 0 ||
        used < sizeof(signature) ||
        memcmp(buffer, signature, sizeof(signature)) != 0) {
        free(buffer);
        return -1;
    }
    *frame = buffer;
    *size = used;
    return 0;
}

static void *stream_frames(void *arg) {
    struct display_stream *stream = arg;
    ws_cli_conn_t *client = stream->client;
    int failures = 0;
    const struct timespec interval = {.tv_sec = 0, .tv_nsec = 500000000};
    for (;;) {
        unsigned char *frame = NULL;
        size_t size = 0;
        pthread_mutex_lock(&streams_lock);
        int stopping = stream->stopping;
        pthread_mutex_unlock(&streams_lock);
        if (stopping) {
            break;
        }
        if (capture_png(&frame, &size) == 0) {
            failures = 0;
            pthread_mutex_lock(&streams_lock);
            stopping = stream->stopping;
            pthread_mutex_unlock(&streams_lock);
            if (stopping) {
                free(frame);
                break;
            }
            int sent = ws_sendframe_bin(client, (const char *)frame, size);
            free(frame);
            if (sent < 0) {
                break;
            }
        } else if (++failures >= 3) {
            fprintf(stderr, "screencap failed three times\n");
            break;
        }
        nanosleep(&interval, NULL);
    }
    pthread_mutex_lock(&streams_lock);
    int disconnected = stream->stopping;
    pthread_mutex_unlock(&streams_lock);
    if (!disconnected) {
        ws_close_client(client);
    }
    return NULL;
}

static void on_open(ws_cli_conn_t *client) {
    int started = 0;
    pthread_mutex_lock(&streams_lock);
    for (size_t i = 0; i < MAX_DISPLAY_CLIENTS; ++i) {
        if (streams[i].client == NULL) {
            streams[i].client = client;
            streams[i].stopping = 0;
            if (pthread_create(&streams[i].thread, NULL, stream_frames,
                               &streams[i]) == 0) {
                started = 1;
            } else {
                streams[i].client = NULL;
            }
            break;
        }
    }
    pthread_mutex_unlock(&streams_lock);
    if (!started) {
        ws_close_client(client);
    }
}

static void on_close(ws_cli_conn_t *client) {
    pthread_mutex_lock(&streams_lock);
    for (size_t i = 0; i < MAX_DISPLAY_CLIENTS; ++i) {
        if (streams[i].client == client) {
            streams[i].stopping = 1;
            pthread_mutex_unlock(&streams_lock);
            // libws releases the client after on_close returns.
            pthread_join(streams[i].thread, NULL);
            pthread_mutex_lock(&streams_lock);
            streams[i].client = NULL;
            break;
        }
    }
    pthread_mutex_unlock(&streams_lock);
}

static void on_message(ws_cli_conn_t *client, const unsigned char *message,
                       uint64_t size, int type) {
    (void)client;
    (void)message;
    (void)size;
    (void)type;
}

int main(int argc, char **argv) {
    struct ws_events callbacks = {
        .onopen = on_open,
        .onclose = on_close,
        .onmessage = on_message,
    };
    if (argc > 2) {
        fprintf(stderr, "usage: %s [SCREENCAP_PATH]\n", argv[0]);
        return 2;
    }
    if (argc == 2) {
        screencap_path = argv[1];
    }
    signal(SIGPIPE, SIG_IGN);
    fprintf(stderr, "PNG screenshot stream on WebSocket port 9091\n");
    return ws_socket(&callbacks, 9091, 0, 1000) == 0 ? 0 : 1;
}
