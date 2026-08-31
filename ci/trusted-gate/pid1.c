#define _GNU_SOURCE

#include <errno.h>
#include <fcntl.h>
#include <grp.h>
#include <inttypes.h>
#include <limits.h>
#include <linux/audit.h>
#include <linux/capability.h>
#include <linux/filter.h>
#include <linux/sched.h>
#include <linux/seccomp.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/file.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#ifndef O_NOFOLLOW
#define O_NOFOLLOW 0
#endif

#define LOG_LIMIT (16U * 1024U * 1024U)
#define MARKER_PREFIX "ALTITUDE_TRUSTED_RESULT "
#define ARRAY_LEN(a) (sizeof(a) / sizeof((a)[0]))
#define EXTRACT_CANDIDATE_SECONDS 60U
#define EXTRACT_BASE_SECONDS 60U
#define REMOVE_TESTS_SECONDS 20U
#define OVERLAY_TESTS_SECONDS 40U
#define BOUNDARY_SECONDS 50U
#define BASE_SECONDS 435U
#define CANDIDATE_SECONDS 435U
#define PID1_GLOBAL_SECONDS 1100U

_Static_assert(EXTRACT_CANDIDATE_SECONDS + EXTRACT_BASE_SECONDS +
                   REMOVE_TESTS_SECONDS + OVERLAY_TESTS_SECONDS +
                   BOUNDARY_SECONDS + BASE_SECONDS + CANDIDATE_SECONDS ==
               PID1_GLOBAL_SECONDS,
               "phase budgets must exactly fit the PID 1 global budget");

#if defined(__x86_64__) && !defined(__X32_SYSCALL_BIT)
#define __X32_SYSCALL_BIT 0x40000000U
#endif

static int output_fd = -1;
static int result_fd = -1;
static size_t log_size;
static bool log_truncated;
static bool log_failed;
static volatile sig_atomic_t terminating;
static uid_t gate_uid;
static gid_t gate_gid;
static int lower_lock_fd = -1;
static int64_t global_deadline_milliseconds = -1;

struct runner_result {
    bool seen;
    uint64_t tests;
    uint64_t failures;
    uint64_t errors;
    uint64_t skipped;
    uint64_t expected_failures;
    uint64_t unexpected_successes;
    unsigned int successful;
};

struct marker_parser {
    const char *label;
    struct runner_result *result;
    char line[1024];
    size_t used;
    bool overflow;
    bool invalid;
    unsigned int markers;
};

static void signal_handler(int signo)
{
    (void)signo;
    terminating = 1;
}

static int write_all(int fd, const void *buffer, size_t length)
{
    const char *cursor = buffer;

    while (length > 0) {
        ssize_t written = write(fd, cursor, length);
        if (written < 0) {
            if (errno == EINTR)
                continue;
            return -1;
        }
        cursor += (size_t)written;
        length -= (size_t)written;
    }
    return 0;
}

static void log_bytes(const void *buffer, size_t length)
{
    static const char notice[] = "\n[gate] output truncated at 16 MiB\n";
    size_t room;

    if (output_fd < 0 || log_truncated)
        return;
    if (log_size + length <= LOG_LIMIT - sizeof(notice) + 1U) {
        if (write_all(output_fd, buffer, length) == 0)
            log_size += length;
        else
            log_failed = true;
        return;
    }

    room = LOG_LIMIT - (sizeof(notice) - 1U) - log_size;
    if (room > length)
        room = length;
    if (room > 0) {
        if (write_all(output_fd, buffer, room) == 0)
            log_size += room;
        else
            log_failed = true;
    }
    if (write_all(output_fd, notice, sizeof(notice) - 1U) == 0)
        log_size += sizeof(notice) - 1U;
    else
        log_failed = true;
    log_truncated = true;
}

static void log_text(const char *text)
{
    log_bytes(text, strlen(text));
}

static void log_phase(const char *label)
{
    char line[256];
    int length = snprintf(line, sizeof(line), "\n[gate] phase=%s\n", label);

    if (length > 0 && (size_t)length < sizeof(line))
        log_bytes(line, (size_t)length);
}

static bool secure_root_directory(const char *path)
{
    struct stat st;

    if (lstat(path, &st) < 0)
        return false;
    return S_ISDIR(st.st_mode) && st.st_uid == 0 &&
           (st.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

static int open_gate_output(int directory_fd, const char *name)
{
    struct stat st;
    int fd = openat(directory_fd, name,
                    O_WRONLY | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);

    if (fd < 0)
        return -1;
    if (fstat(fd, &st) < 0 || !S_ISREG(st.st_mode) || st.st_uid != 0 ||
        st.st_nlink != 1 || fchmod(fd, 0600) < 0 || ftruncate(fd, 0) < 0 ||
        lseek(fd, 0, SEEK_SET) < 0) {
        int saved = errno == 0 ? EPERM : errno;
        close(fd);
        errno = saved;
        return -1;
    }
    if (fd < 3) {
        int replacement = fcntl(fd, F_DUPFD_CLOEXEC, 3);
        int saved = errno;
        close(fd);
        if (replacement < 0) {
            errno = saved;
            return -1;
        }
        fd = replacement;
    }
    return fd;
}

static int ensure_standard_descriptors(void)
{
    int null_fd = -1;

    for (int fd = STDIN_FILENO; fd <= STDERR_FILENO; ++fd) {
        if (fcntl(fd, F_GETFD) >= 0)
            continue;
        if (errno != EBADF)
            return -1;
        if (null_fd < 0) {
            null_fd = open("/dev/null", O_RDWR | O_CLOEXEC);
            if (null_fd < 0)
                return -1;
        }
        if (dup2(null_fd, fd) < 0) {
            if (null_fd > STDERR_FILENO)
                close(null_fd);
            return -1;
        }
    }
    if (null_fd > STDERR_FILENO)
        close(null_fd);
    return 0;
}

static bool trusted_regular_file(const char *path)
{
    struct stat st;

    if (lstat(path, &st) < 0)
        return false;
    return S_ISREG(st.st_mode) && st.st_uid == 0 &&
           (st.st_mode & (S_IWGRP | S_IWOTH)) == 0;
}

static bool trusted_executable(const char *path)
{
    struct stat st;

    if (stat(path, &st) < 0)
        return false;
    return S_ISREG(st.st_mode) && st.st_uid == 0 &&
           (st.st_mode & (S_IWGRP | S_IWOTH)) == 0 && access(path, X_OK) == 0;
}

static bool real_directory(const char *path)
{
    struct stat st;

    return lstat(path, &st) == 0 && S_ISDIR(st.st_mode);
}

static bool parse_id(const char *name, uint32_t *value)
{
    const char *raw = getenv(name);
    uint64_t parsed = 0;

    if (raw == NULL || raw[0] == '\0' || raw[0] == '-' || raw[0] == '+')
        return false;
    for (const char *cursor = raw; *cursor != '\0'; ++cursor) {
        unsigned int digit;

        if (*cursor < '0' || *cursor > '9')
            return false;
        digit = (unsigned int)(*cursor - '0');
        if (parsed > (UINT32_MAX - 1U - digit) / 10U)
            return false;
        parsed = parsed * 10U + digit;
    }
    if (parsed == 0 || parsed >= UINT32_MAX)
        return false;
    *value = (uint32_t)parsed;
    return true;
}

static bool parse_inherited_fd(const char *name, int *value)
{
    const char *raw = getenv(name);
    uint64_t parsed = 0;

    if (raw == NULL || raw[0] == '\0' || raw[0] == '-' || raw[0] == '+')
        return false;
    for (const char *cursor = raw; *cursor != '\0'; ++cursor) {
        unsigned int digit;

        if (*cursor < '0' || *cursor > '9')
            return false;
        digit = (unsigned int)(*cursor - '0');
        if (parsed > ((uint64_t)INT_MAX - digit) / 10U)
            return false;
        parsed = parsed * 10U + digit;
    }
    if (parsed < 3 || parsed > INT_MAX)
        return false;
    *value = (int)parsed;
    return true;
}

static bool validate_lower_lock(void)
{
    struct stat lower;
    struct stat overlay;
    char proc_path[64];
    int descriptor_flags;
    int status;
    pid_t verifier;
    struct flock lower_record_lock = {
        .l_type = F_WRLCK,
        .l_whence = SEEK_SET,
        .l_start = 0,
        .l_len = 0,
    };

    if (!parse_inherited_fd("ALTITUDE_GATE_LOWER_LOCK_FD", &lower_lock_fd))
        return false;
    descriptor_flags = fcntl(lower_lock_fd, F_GETFD);
    if (descriptor_flags < 0 || fstat(lower_lock_fd, &lower) < 0 ||
        !S_ISREG(lower.st_mode) || lower.st_uid != 0 ||
        (fcntl(lower_lock_fd, F_GETFL, 0) & O_ACCMODE) != O_RDWR ||
        stat("/usr/bin/python3", &overlay) < 0 || !S_ISREG(overlay.st_mode) ||
        (lower.st_dev == overlay.st_dev && lower.st_ino == overlay.st_ino))
        return false;
    if (fcntl(lower_lock_fd, F_SETLK, &lower_record_lock) < 0)
        return false;
    if (snprintf(proc_path, sizeof(proc_path), "/proc/self/fd/%d", lower_lock_fd) < 0)
        return false;

    verifier = fork();
    if (verifier < 0)
        return false;
    if (verifier == 0) {
        int competing_fd = open(proc_path, O_RDWR | O_CLOEXEC);
        struct flock competing_record_lock = {
            .l_type = F_RDLCK,
            .l_whence = SEEK_SET,
            .l_start = 0,
            .l_len = 0,
        };

        if (competing_fd < 0)
            _exit(1);
        close(lower_lock_fd);
        errno = 0;
        if (flock(competing_fd, LOCK_EX | LOCK_NB) == 0) {
            (void)flock(competing_fd, LOCK_UN);
            close(competing_fd);
            _exit(2);
        }
        if (errno != EWOULDBLOCK && errno != EAGAIN) {
            close(competing_fd);
            _exit(3);
        }
        errno = 0;
        if (fcntl(competing_fd, F_SETLK, &competing_record_lock) == 0) {
            competing_record_lock.l_type = F_UNLCK;
            (void)fcntl(competing_fd, F_SETLK, &competing_record_lock);
            close(competing_fd);
            _exit(4);
        }
        if (errno != EACCES && errno != EAGAIN) {
            close(competing_fd);
            _exit(5);
        }
        close(competing_fd);
        _exit(0);
    }
    while (waitpid(verifier, &status, 0) < 0) {
        if (errno != EINTR)
            return false;
    }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0;
}

static int make_suite_directory(const char *path)
{
    if (mkdir(path, 0700) < 0)
        return -1;
    if (chown(path, gate_uid, gate_gid) < 0 || chmod(path, 0700) < 0)
        return -1;
    return 0;
}

static int make_phase_state(const char *path)
{
    static const char *const children[] = {
        "home", "tmp", "runtime", "config", "cache", "data", "altitude",
    };
    char child_path[PATH_MAX];

    if (mkdir(path, 0700) < 0 || chown(path, gate_uid, gate_gid) < 0 ||
        chmod(path, 0700) < 0)
        return -1;
    for (size_t index = 0; index < ARRAY_LEN(children); ++index) {
        int length = snprintf(child_path, sizeof(child_path), "%s/%s", path,
                              children[index]);
        if (length < 0 || (size_t)length >= sizeof(child_path)) {
            errno = ENAMETOOLONG;
            return -1;
        }
        if (mkdir(child_path, 0700) < 0 || chown(child_path, gate_uid, gate_gid) < 0 ||
            chmod(child_path, 0700) < 0)
            return -1;
    }
    {
        int length = snprintf(child_path, sizeof(child_path), "%s/altitude/jobs", path);
        if (length < 0 || (size_t)length >= sizeof(child_path)) {
            errno = ENAMETOOLONG;
            return -1;
        }
        if (mkdir(child_path, 0700) < 0 || chown(child_path, gate_uid, gate_gid) < 0 ||
            chmod(child_path, 0700) < 0)
            return -1;
    }
    return 0;
}

static void close_from_three(void)
{
#ifdef __NR_close_range
    if (syscall(__NR_close_range, 3U, ~0U, 0U) == 0)
        return;
    if (errno != ENOSYS && errno != EINVAL)
        _exit(126);
#endif
    {
        struct rlimit limit;
        rlim_t maximum = 65536;

        if (getrlimit(RLIMIT_NOFILE, &limit) == 0)
            maximum = limit.rlim_cur;
        if (maximum == RLIM_INFINITY || maximum > 1048576)
            maximum = 1048576;
        for (int fd = 3; (rlim_t)fd < maximum; ++fd)
            close(fd);
    }
}

static int last_capability(void)
{
    char buffer[32];
    int fd = open("/proc/sys/kernel/cap_last_cap", O_RDONLY | O_CLOEXEC);
    long parsed;
    char *end = NULL;
    ssize_t length;

    if (fd < 0)
        return 63;
    length = read(fd, buffer, sizeof(buffer) - 1U);
    close(fd);
    if (length <= 0)
        return 63;
    buffer[length] = '\0';
    errno = 0;
    parsed = strtol(buffer, &end, 10);
    if (errno != 0 || end == buffer || parsed < 0 || parsed > 255)
        return 63;
    return (int)parsed;
}

static int zero_and_verify_capabilities(void)
{
    struct __user_cap_header_struct header = {
        .version = _LINUX_CAPABILITY_VERSION_3,
        .pid = 0,
    };
    struct __user_cap_data_struct data[2] = {{0}};

    if (syscall(__NR_capset, &header, data) < 0)
        return -1;
    memset(data, 0xff, sizeof(data));
    if (syscall(__NR_capget, &header, data) < 0)
        return -1;
    for (size_t index = 0; index < ARRAY_LEN(data); ++index) {
        if (data[index].effective != 0 || data[index].permitted != 0 ||
            data[index].inheritable != 0) {
            errno = EPERM;
            return -1;
        }
    }
    return 0;
}

struct filter_builder {
    struct sock_filter instructions[192];
    size_t count;
};

static void filter_add(struct filter_builder *builder, struct sock_filter instruction)
{
    if (builder->count >= ARRAY_LEN(builder->instructions))
        _exit(126);
    builder->instructions[builder->count++] = instruction;
}

static void filter_reject_syscall(struct filter_builder *builder, int number,
                                  unsigned int error_number)
{
    filter_add(builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                      (uint32_t)number, 0, 1));
    filter_add(builder, (struct sock_filter)BPF_STMT(BPF_RET | BPF_K,
                                                      SECCOMP_RET_ERRNO |
                                                          (error_number &
                                                           SECCOMP_RET_DATA)));
}

static void filter_deny_syscall(struct filter_builder *builder, int number)
{
    filter_reject_syscall(builder, number, EPERM);
}

static void filter_socket_domains(struct filter_builder *builder, int number,
                                  bool socket_pair)
{
    size_t dispatch = builder->count;

    filter_add(builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                      (uint32_t)number, 0, 0));
    filter_add(builder, (struct sock_filter)BPF_STMT(
                            BPF_LD | BPF_W | BPF_ABS,
                            offsetof(struct seccomp_data, args[0])));
    filter_add(builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                      AF_UNIX, 0, 1));
    filter_add(builder, (struct sock_filter)BPF_STMT(BPF_RET | BPF_K,
                                                      SECCOMP_RET_ALLOW));
    if (!socket_pair) {
        filter_add(builder,
                   (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                AF_INET, 0, 1));
        filter_add(builder, (struct sock_filter)BPF_STMT(BPF_RET | BPF_K,
                                                          SECCOMP_RET_ALLOW));
        filter_add(builder,
                   (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                AF_INET6, 0, 1));
        filter_add(builder, (struct sock_filter)BPF_STMT(BPF_RET | BPF_K,
                                                          SECCOMP_RET_ALLOW));
    }
    filter_add(builder, (struct sock_filter)BPF_STMT(
                            BPF_RET | BPF_K,
                            SECCOMP_RET_ERRNO | (EPERM & SECCOMP_RET_DATA)));
    builder->instructions[dispatch].jf =
        (uint8_t)(builder->count - dispatch - 1U);
}

static void filter_clone_block(struct filter_builder *builder)
{
#ifdef __NR_clone
    size_t dispatch = builder->count;
    uint32_t namespace_flags = CLONE_NEWNS | CLONE_NEWCGROUP | CLONE_NEWUTS |
                               CLONE_NEWIPC | CLONE_NEWUSER | CLONE_NEWPID |
                               CLONE_NEWNET;

#ifdef CLONE_NEWTIME
    namespace_flags |= CLONE_NEWTIME;
#endif

    filter_add(builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                      __NR_clone, 0, 0));
    filter_add(builder, (struct sock_filter)BPF_STMT(
                            BPF_LD | BPF_W | BPF_ABS,
                            offsetof(struct seccomp_data, args[0])));
    filter_add(builder, (struct sock_filter)BPF_STMT(BPF_ALU | BPF_AND | BPF_K,
                                                      namespace_flags));
    filter_add(builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                      0, 1, 0));
    filter_add(builder, (struct sock_filter)BPF_STMT(BPF_RET | BPF_K,
                                                      SECCOMP_RET_ERRNO |
                                                          (EPERM & SECCOMP_RET_DATA)));
    filter_add(builder, (struct sock_filter)BPF_STMT(
                            BPF_LD | BPF_W | BPF_ABS,
                            offsetof(struct seccomp_data, nr)));
    builder->instructions[dispatch].jf =
        (uint8_t)(builder->count - dispatch - 1U);
#else
    (void)builder;
#endif
}

static int install_seccomp(void)
{
    struct filter_builder builder = {0};
    struct sock_fprog program;

    filter_add(&builder, (struct sock_filter)BPF_STMT(
                             BPF_LD | BPF_W | BPF_ABS,
                             offsetof(struct seccomp_data, arch)));
#if defined(__x86_64__)
    filter_add(&builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                      AUDIT_ARCH_X86_64, 1, 0));
#elif defined(__aarch64__)
    filter_add(&builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JEQ | BPF_K,
                                                      AUDIT_ARCH_AARCH64, 1, 0));
#else
#error Unsupported architecture for the trusted gate seccomp filter
#endif
    filter_add(&builder,
               (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_KILL_PROCESS));
    filter_add(&builder, (struct sock_filter)BPF_STMT(
                             BPF_LD | BPF_W | BPF_ABS,
                             offsetof(struct seccomp_data, nr)));
#if defined(__x86_64__)
    filter_add(&builder, (struct sock_filter)BPF_JUMP(BPF_JMP | BPF_JSET | BPF_K,
                                                      __X32_SYSCALL_BIT, 0, 1));
    filter_add(&builder,
               (struct sock_filter)BPF_STMT(BPF_RET | BPF_K,
                                            SECCOMP_RET_KILL_PROCESS));
#endif

#ifdef __NR_socket
    filter_socket_domains(&builder, __NR_socket, false);
#endif
#ifdef __NR_socketpair
    filter_socket_domains(&builder, __NR_socketpair, true);
#endif
#ifdef __NR_socketcall
    filter_deny_syscall(&builder, __NR_socketcall);
#endif
#ifdef __NR_io_uring_setup
    filter_deny_syscall(&builder, __NR_io_uring_setup);
#endif
#ifdef __NR_io_uring_enter
    filter_deny_syscall(&builder, __NR_io_uring_enter);
#endif
#ifdef __NR_io_uring_register
    filter_deny_syscall(&builder, __NR_io_uring_register);
#endif

#ifdef __NR_mount
    filter_deny_syscall(&builder, __NR_mount);
#endif
#ifdef __NR_umount2
    filter_deny_syscall(&builder, __NR_umount2);
#endif
#ifdef __NR_pivot_root
    filter_deny_syscall(&builder, __NR_pivot_root);
#endif
#ifdef __NR_chroot
    filter_deny_syscall(&builder, __NR_chroot);
#endif
#ifdef __NR_fsopen
    filter_deny_syscall(&builder, __NR_fsopen);
#endif
#ifdef __NR_fsconfig
    filter_deny_syscall(&builder, __NR_fsconfig);
#endif
#ifdef __NR_fsmount
    filter_deny_syscall(&builder, __NR_fsmount);
#endif
#ifdef __NR_move_mount
    filter_deny_syscall(&builder, __NR_move_mount);
#endif
#ifdef __NR_open_tree
    filter_deny_syscall(&builder, __NR_open_tree);
#endif
#ifdef __NR_mount_setattr
    filter_deny_syscall(&builder, __NR_mount_setattr);
#endif
#ifdef __NR_unshare
    filter_deny_syscall(&builder, __NR_unshare);
#endif
#ifdef __NR_setns
    filter_deny_syscall(&builder, __NR_setns);
#endif
#ifdef __NR_ptrace
    filter_deny_syscall(&builder, __NR_ptrace);
#endif
#ifdef __NR_add_key
    filter_deny_syscall(&builder, __NR_add_key);
#endif
#ifdef __NR_request_key
    filter_deny_syscall(&builder, __NR_request_key);
#endif
#ifdef __NR_keyctl
    filter_deny_syscall(&builder, __NR_keyctl);
#endif
#ifdef __NR_bpf
    filter_deny_syscall(&builder, __NR_bpf);
#endif
#ifdef __NR_perf_event_open
    filter_deny_syscall(&builder, __NR_perf_event_open);
#endif
#ifdef __NR_reboot
    filter_deny_syscall(&builder, __NR_reboot);
#endif
#ifdef __NR_kexec_load
    filter_deny_syscall(&builder, __NR_kexec_load);
#endif
#ifdef __NR_kexec_file_load
    filter_deny_syscall(&builder, __NR_kexec_file_load);
#endif
    /* clone3 carries its flags indirectly, which classic seccomp cannot inspect. */
#ifdef __NR_clone3
    filter_reject_syscall(&builder, __NR_clone3, ENOSYS);
#endif
    filter_clone_block(&builder);
    filter_add(&builder,
               (struct sock_filter)BPF_STMT(BPF_RET | BPF_K, SECCOMP_RET_ALLOW));

    program.len = (unsigned short)builder.count;
    program.filter = builder.instructions;
    if (prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, &program) < 0)
        return -1;
    return 0;
}

static void child_error(const char *message)
{
    int saved = errno;
    dprintf(STDERR_FILENO, "trusted-gate: %s: %s\n", message, strerror(saved));
    _exit(126);
}

static void reset_child_signals(void)
{
    struct sigaction action = {.sa_handler = SIG_DFL};
    int signals[] = {SIGTERM, SIGINT, SIGHUP, SIGPIPE, SIGCHLD};

    sigemptyset(&action.sa_mask);
    for (size_t index = 0; index < ARRAY_LEN(signals); ++index)
        (void)sigaction(signals[index], &action, NULL);
    {
        sigset_t empty;
        sigemptyset(&empty);
        (void)sigprocmask(SIG_SETMASK, &empty, NULL);
    }
}

static void prepare_child(int stdout_pipe, int stderr_pipe, const char *state_root)
{
    char uid_text[32];
    char gid_text[32];
    char home_path[PATH_MAX];
    char tmp_path[PATH_MAX];
    char runtime_path[PATH_MAX];
    char config_path[PATH_MAX];
    char cache_path[PATH_MAX];
    char data_path[PATH_MAX];
    char altitude_path[PATH_MAX];
    char jobs_path[PATH_MAX];
    int null_fd;
    int cap_last;

    reset_child_signals();
    if (setpgid(0, 0) < 0)
        child_error("setpgid");
    null_fd = open("/dev/null", O_RDONLY | O_CLOEXEC);
    if (null_fd < 0)
        child_error("open /dev/null");
    if (dup2(null_fd, STDIN_FILENO) < 0 ||
        dup2(stdout_pipe, STDOUT_FILENO) < 0 ||
        dup2(stderr_pipe, STDERR_FILENO) < 0)
        child_error("establish stdio");
    close_from_three();

    if (clearenv() != 0)
        child_error("clear environment");
    snprintf(uid_text, sizeof(uid_text), "%" PRIu32, (uint32_t)gate_uid);
    snprintf(gid_text, sizeof(gid_text), "%" PRIu32, (uint32_t)gate_gid);
    if (state_root == NULL)
        state_root = "/tmp";
    if (snprintf(home_path, sizeof(home_path), "%s/home", state_root) < 0 ||
        snprintf(tmp_path, sizeof(tmp_path), "%s/tmp", state_root) < 0 ||
        snprintf(runtime_path, sizeof(runtime_path), "%s/runtime", state_root) < 0 ||
        snprintf(config_path, sizeof(config_path), "%s/config", state_root) < 0 ||
        snprintf(cache_path, sizeof(cache_path), "%s/cache", state_root) < 0 ||
        snprintf(data_path, sizeof(data_path), "%s/data", state_root) < 0 ||
        snprintf(altitude_path, sizeof(altitude_path), "%s/altitude", state_root) < 0 ||
        snprintf(jobs_path, sizeof(jobs_path), "%s/altitude/jobs", state_root) < 0 ||
        strlen(home_path) >= sizeof(home_path) - 1U ||
        strlen(tmp_path) >= sizeof(tmp_path) - 1U ||
        strlen(runtime_path) >= sizeof(runtime_path) - 1U ||
        strlen(config_path) >= sizeof(config_path) - 1U ||
        strlen(cache_path) >= sizeof(cache_path) - 1U ||
        strlen(data_path) >= sizeof(data_path) - 1U ||
        strlen(altitude_path) >= sizeof(altitude_path) - 1U ||
        strlen(jobs_path) >= sizeof(jobs_path) - 1U) {
        errno = ENAMETOOLONG;
        child_error("build state paths");
    }
    if (setenv("PATH", "/usr/bin:/bin", 1) < 0 ||
        setenv("HOME", home_path, 1) < 0 || setenv("TMPDIR", tmp_path, 1) < 0 ||
        setenv("XDG_RUNTIME_DIR", runtime_path, 1) < 0 ||
        setenv("XDG_CONFIG_HOME", config_path, 1) < 0 ||
        setenv("XDG_CACHE_HOME", cache_path, 1) < 0 ||
        setenv("XDG_DATA_HOME", data_path, 1) < 0 ||
        setenv("ALTITUDE_HOME", altitude_path, 1) < 0 ||
        setenv("ALTITUDE_JOBS_DIR", jobs_path, 1) < 0 ||
        setenv("ALTITUDE_TEST_MODE", "1", 1) < 0 ||
        setenv("ALTITUDE_TEST_ROOT", state_root, 1) < 0 ||
        setenv("ALTITUDE_TRUSTED_REMOTE_TEST", "1", 1) < 0 ||
        setenv("ALTITUDE_TEST_SIGNAL_MODE", "allowed", 1) < 0 ||
        setenv("ALTITUDE_TEST_NETWORK_MODE", "loopback", 1) < 0 ||
        setenv("GIT_CONFIG_GLOBAL", "/dev/null", 1) < 0 ||
        setenv("GIT_CONFIG_SYSTEM", "/dev/null", 1) < 0 ||
        setenv("GIT_ALLOW_PROTOCOL", "file", 1) < 0 ||
        setenv("GIT_PROTOCOL_FROM_USER", "0", 1) < 0 ||
        setenv("GIT_TERMINAL_PROMPT", "0", 1) < 0 ||
        setenv("GIT_SSH_COMMAND", "/bin/false", 1) < 0 ||
        setenv("LANG", "C.UTF-8", 1) < 0 || setenv("LC_ALL", "C.UTF-8", 1) < 0 ||
        setenv("PYTHONDONTWRITEBYTECODE", "1", 1) < 0 ||
        setenv("PYTHONNOUSERSITE", "1", 1) < 0 ||
        setenv("ALTITUDE_GATE_UID", uid_text, 1) < 0 ||
        setenv("ALTITUDE_GATE_GID", gid_text, 1) < 0)
        child_error("build environment");

    cap_last = last_capability();
    if (setgroups(0, NULL) < 0)
        child_error("drop supplementary groups");
    for (int capability = 0; capability <= cap_last; ++capability) {
        if (prctl(PR_CAPBSET_DROP, capability, 0, 0, 0) < 0)
            child_error("drop capability bounding set");
    }
    if (prctl(PR_SET_KEEPCAPS, 0, 0, 0, 0) < 0)
        child_error("disable keepcaps");
    if (setresgid(gate_gid, gate_gid, gate_gid) < 0)
        child_error("drop gid");
    if (setresuid(gate_uid, gate_uid, gate_uid) < 0)
        child_error("drop uid");
    if (zero_and_verify_capabilities() < 0)
        child_error("clear capability sets");
    if (prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0) < 0)
        child_error("clear ambient capabilities");
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) < 0)
        child_error("disable dumpability");
    if (prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0)
        child_error("set no_new_privs");
    if (getuid() != gate_uid || geteuid() != gate_uid || getgid() != gate_gid ||
        getegid() != gate_gid || getgroups(0, NULL) != 0) {
        errno = EPERM;
        child_error("verify identity drop");
    }
    for (int capability = 0; capability <= cap_last; ++capability) {
        int present = prctl(PR_CAPBSET_READ, capability, 0, 0, 0);
        if (present != 0) {
            if (present < 0 && errno == EINVAL)
                continue;
            errno = EPERM;
            child_error("verify capability bounding set");
        }
        present = prctl(PR_CAP_AMBIENT, PR_CAP_AMBIENT_IS_SET, capability, 0, 0);
        if (present != 0) {
            if (present < 0 && errno == EINVAL)
                continue;
            errno = EPERM;
            child_error("verify ambient capability set");
        }
    }
    if (prctl(PR_GET_DUMPABLE, 0, 0, 0, 0) != 0 ||
        prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1) {
        errno = EPERM;
        child_error("verify process restrictions");
    }
    if (install_seccomp() < 0)
        child_error("install seccomp");
    if (prctl(PR_GET_SECCOMP, 0, 0, 0, 0) != SECCOMP_MODE_FILTER) {
        errno = EPERM;
        child_error("verify seccomp");
    }
}

static bool parse_decimal(const char **cursor, uint64_t *value)
{
    uint64_t parsed = 0;
    const char *start = *cursor;

    if (**cursor < '0' || **cursor > '9')
        return false;
    while (**cursor >= '0' && **cursor <= '9') {
        unsigned int digit = (unsigned int)(**cursor - '0');
        if (parsed > (UINT64_MAX - digit) / 10U)
            return false;
        parsed = parsed * 10U + digit;
        ++*cursor;
    }
    if (*cursor == start)
        return false;
    *value = parsed;
    return true;
}

static bool consume_literal(const char **cursor, const char *literal)
{
    size_t length = strlen(literal);

    if (strncmp(*cursor, literal, length) != 0)
        return false;
    *cursor += length;
    return true;
}

static bool parse_marker_line(const char *line, const char *label,
                              struct runner_result *result)
{
    struct runner_result parsed = {0};
    const char *cursor = line;
    uint64_t successful;
    size_t label_length = strlen(label);

    if (!consume_literal(&cursor, MARKER_PREFIX) ||
        !consume_literal(&cursor, "label=") ||
        strncmp(cursor, label, label_length) != 0 ||
        cursor[label_length] != ' ')
        return false;
    cursor += label_length + 1U;
    if (!consume_literal(&cursor, "tests=") || !parse_decimal(&cursor, &parsed.tests) ||
        !consume_literal(&cursor, " failures=") ||
        !parse_decimal(&cursor, &parsed.failures) ||
        !consume_literal(&cursor, " errors=") ||
        !parse_decimal(&cursor, &parsed.errors) ||
        !consume_literal(&cursor, " skipped=") ||
        !parse_decimal(&cursor, &parsed.skipped) ||
        !consume_literal(&cursor, " expected_failures=") ||
        !parse_decimal(&cursor, &parsed.expected_failures) ||
        !consume_literal(&cursor, " unexpected_successes=") ||
        !parse_decimal(&cursor, &parsed.unexpected_successes) ||
        !consume_literal(&cursor, " successful=") ||
        !parse_decimal(&cursor, &successful) || *cursor != '\0' || successful > 1)
        return false;
    parsed.successful = (unsigned int)successful;
    parsed.seen = true;
    *result = parsed;
    return true;
}

static void marker_finish_line(struct marker_parser *parser)
{
    if (!parser->overflow) {
        parser->line[parser->used] = '\0';
        if (strncmp(parser->line, MARKER_PREFIX, strlen(MARKER_PREFIX)) == 0) {
            struct runner_result parsed;
            ++parser->markers;
            if (!parse_marker_line(parser->line, parser->label, &parsed))
                parser->invalid = true;
            else
                *parser->result = parsed;
        }
    } else if (parser->used >= strlen(MARKER_PREFIX) &&
               strncmp(parser->line, MARKER_PREFIX, strlen(MARKER_PREFIX)) == 0) {
        parser->invalid = true;
    }
    parser->used = 0;
    parser->overflow = false;
}

static void marker_feed(struct marker_parser *parser, const char *data, size_t length)
{
    if (parser == NULL)
        return;
    for (size_t index = 0; index < length; ++index) {
        unsigned char byte = (unsigned char)data[index];
        if (byte == '\n') {
            marker_finish_line(parser);
            continue;
        }
        if (byte == '\0') {
            parser->overflow = true;
            continue;
        }
        if (parser->used + 1U < sizeof(parser->line))
            parser->line[parser->used++] = (char)byte;
        else
            parser->overflow = true;
    }
}

static int64_t monotonic_milliseconds(void)
{
    struct timespec now;

    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
        return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static bool set_nonblocking(int fd)
{
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0)
        return false;
    return true;
}

static void drain_pipe(int fd, bool *eof, struct marker_parser *parser)
{
    char buffer[8192];

    while (!*eof) {
        ssize_t length = read(fd, buffer, sizeof(buffer));
        if (length > 0) {
            log_bytes(buffer, (size_t)length);
            marker_feed(parser, buffer, (size_t)length);
            continue;
        }
        if (length == 0) {
            *eof = true;
            return;
        }
        if (errno == EINTR)
            continue;
        if (errno == EAGAIN || errno == EWOULDBLOCK)
            return;
        *eof = true;
        return;
    }
}

static void kill_and_reap_descendants(pid_t group)
{
    int status;
    struct timespec pause_time = {.tv_sec = 0, .tv_nsec = 100000000};

    if (group > 1)
        (void)kill(-group, SIGTERM);
    (void)kill(-1, SIGTERM);
    (void)nanosleep(&pause_time, NULL);
    if (group > 1)
        (void)kill(-group, SIGKILL);
    (void)kill(-1, SIGKILL);
    for (;;) {
        pid_t reaped = waitpid(-1, &status, 0);
        if (reaped > 0)
            continue;
        if (reaped < 0 && errno == EINTR)
            continue;
        break;
    }
}

static bool descendants_remain(void)
{
    int status;

    for (;;) {
        pid_t reaped = waitpid(-1, &status, WNOHANG);
        if (reaped > 0)
            continue;
        if (reaped == 0)
            return true;
        if (errno == EINTR)
            continue;
        return false;
    }
}

static bool run_command(const char *label, char *const argv[], unsigned int timeout_seconds,
                        const char *state_root, const char *marker_label,
                        struct runner_result *runner_result)
{
    int stdout_pipe[2] = {-1, -1};
    int stderr_pipe[2] = {-1, -1};
    struct marker_parser parser = {
        .label = marker_label,
        .result = runner_result,
    };
    struct marker_parser *parser_pointer = marker_label == NULL ? NULL : &parser;
    int status = 0;
    bool child_exited = false;
    bool stdout_eof = false;
    bool stderr_eof = false;
    bool timed_out = false;
    bool command_error = false;
    bool lingering = false;
    int64_t deadline;
    pid_t child;

    log_phase(label);
    if (pipe2(stdout_pipe, O_CLOEXEC) < 0 || pipe2(stderr_pipe, O_CLOEXEC) < 0) {
        log_text("[gate] pipe creation failed\n");
        goto fail;
    }
    child = fork();
    if (child < 0) {
        log_text("[gate] fork failed\n");
        goto fail;
    }
    if (child == 0) {
        close(stdout_pipe[0]);
        close(stderr_pipe[0]);
        prepare_child(stdout_pipe[1], stderr_pipe[1], state_root);
        execv(argv[0], argv);
        child_error("exec");
    }

    (void)setpgid(child, child);
    close(stdout_pipe[1]);
    stdout_pipe[1] = -1;
    close(stderr_pipe[1]);
    stderr_pipe[1] = -1;
    if (!set_nonblocking(stdout_pipe[0]) || !set_nonblocking(stderr_pipe[0]))
        command_error = true;
    deadline = monotonic_milliseconds();
    if (deadline < 0 || global_deadline_milliseconds < 0)
        command_error = true;
    else {
        deadline += (int64_t)timeout_seconds * 1000;
        if (deadline > global_deadline_milliseconds)
            deadline = global_deadline_milliseconds;
    }

    while (!child_exited && !command_error) {
        struct pollfd poll_fds[2] = {
            {.fd = stdout_eof ? -1 : stdout_pipe[0], .events = POLLIN | POLLHUP},
            {.fd = stderr_eof ? -1 : stderr_pipe[0], .events = POLLIN | POLLHUP},
        };
        int64_t now = monotonic_milliseconds();
        int remaining;

        if (terminating || now < 0 || now >= deadline) {
            timed_out = true;
            break;
        }
        remaining = (int)(deadline - now);
        if (remaining > 100)
            remaining = 100;
        if (poll(poll_fds, ARRAY_LEN(poll_fds), remaining) < 0 && errno != EINTR) {
            command_error = true;
            break;
        }
        drain_pipe(stdout_pipe[0], &stdout_eof, parser_pointer);
        drain_pipe(stderr_pipe[0], &stderr_eof, NULL);
        for (;;) {
            pid_t waited = waitpid(child, &status, WNOHANG);
            if (waited == child) {
                child_exited = true;
                break;
            }
            if (waited < 0 && errno == EINTR)
                continue;
            if (waited < 0) {
                child_exited = true;
                status = -1;
                command_error = true;
            }
            break;
        }
    }

    if (!child_exited) {
        if (child > 1)
            (void)kill(-child, SIGTERM);
        (void)kill(-1, SIGTERM);
        {
            struct timespec pause_time = {.tv_sec = 0, .tv_nsec = 100000000};
            (void)nanosleep(&pause_time, NULL);
        }
        if (child > 1)
            (void)kill(-child, SIGKILL);
        (void)kill(-1, SIGKILL);
        while (waitpid(child, &status, 0) < 0 && errno == EINTR)
            ;
        child_exited = true;
    }

    drain_pipe(stdout_pipe[0], &stdout_eof, parser_pointer);
    drain_pipe(stderr_pipe[0], &stderr_eof, NULL);
    if (parser_pointer != NULL && parser_pointer->used != 0)
        marker_finish_line(parser_pointer);
    close(stdout_pipe[0]);
    stdout_pipe[0] = -1;
    close(stderr_pipe[0]);
    stderr_pipe[0] = -1;

    lingering = descendants_remain();
    if (lingering)
        log_text("[gate] descendant survived command; terminating namespace children\n");
    if (lingering || timed_out)
        kill_and_reap_descendants(child);

    if (timed_out) {
        log_text("[gate] phase deadline or termination reached\n");
        return false;
    }
    if (command_error) {
        log_text("[gate] phase monitoring failed\n");
        return false;
    }
    if (status == -1 || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
        log_text("[gate] phase exited unsuccessfully\n");
        return false;
    }
    if (lingering)
        return false;
    if (parser_pointer != NULL) {
        if (parser_pointer->invalid || parser_pointer->markers != 1 ||
            !runner_result->seen) {
            log_text("[gate] trusted runner marker missing, duplicated, or malformed\n");
            return false;
        }
        if (runner_result->tests == 0 || runner_result->tests > 1000000U ||
            runner_result->failures > runner_result->tests ||
            runner_result->errors > runner_result->tests ||
            runner_result->skipped >= runner_result->tests ||
            runner_result->expected_failures > runner_result->tests ||
            runner_result->unexpected_successes > runner_result->tests ||
            runner_result->failures + runner_result->errors + runner_result->skipped +
                    runner_result->expected_failures +
                    runner_result->unexpected_successes >
                runner_result->tests ||
            runner_result->successful != 1 ||
            runner_result->failures != 0 || runner_result->errors != 0 ||
            runner_result->unexpected_successes != 0) {
            log_text("[gate] trusted runner reported a failed or empty suite\n");
            return false;
        }
    }
    return true;

fail:
    if (stdout_pipe[0] >= 0)
        close(stdout_pipe[0]);
    if (stdout_pipe[1] >= 0)
        close(stdout_pipe[1]);
    if (stderr_pipe[0] >= 0)
        close(stderr_pipe[0]);
    if (stderr_pipe[1] >= 0)
        close(stderr_pipe[1]);
    return false;
}

static int write_result(bool boundary_ok, bool base_ok,
                        const struct runner_result *base, bool candidate_ok,
                        const struct runner_result *candidate, bool gate_ok)
{
    char buffer[2048];
    int length = snprintf(
        buffer, sizeof(buffer),
        "schema_version=1\n"
        "boundary_status=%u\n"
        "base_status=%u\n"
        "base_tests=%" PRIu64 "\n"
        "base_failures=%" PRIu64 "\n"
        "base_errors=%" PRIu64 "\n"
        "base_skipped=%" PRIu64 "\n"
        "base_expected_failures=%" PRIu64 "\n"
        "base_unexpected_successes=%" PRIu64 "\n"
        "base_successful=%u\n"
        "candidate_status=%u\n"
        "candidate_tests=%" PRIu64 "\n"
        "candidate_failures=%" PRIu64 "\n"
        "candidate_errors=%" PRIu64 "\n"
        "candidate_skipped=%" PRIu64 "\n"
        "candidate_expected_failures=%" PRIu64 "\n"
        "candidate_unexpected_successes=%" PRIu64 "\n"
        "candidate_successful=%u\n"
        "log_truncated=%u\n"
        "gate_ok=%u\n",
        boundary_ok ? 0U : 1U, base_ok ? 0U : 1U, base->tests,
        base->failures, base->errors, base->skipped, base->expected_failures,
        base->unexpected_successes, base->successful, candidate_ok ? 0U : 1U,
        candidate->tests, candidate->failures, candidate->errors,
        candidate->skipped, candidate->expected_failures,
        candidate->unexpected_successes, candidate->successful,
        log_truncated ? 1U : 0U, gate_ok ? 1U : 0U);

    if (length < 0 || (size_t)length >= sizeof(buffer)) {
        errno = EOVERFLOW;
        return -1;
    }
    if (ftruncate(result_fd, 0) < 0 || lseek(result_fd, 0, SEEK_SET) < 0 ||
        write_all(result_fd, buffer, (size_t)length) < 0 || fsync(result_fd) < 0)
        return -1;
    return 0;
}

int main(void)
{
    static const char candidate_archive[] = "/inputs/candidate.tar";
    static const char base_tests_archive[] = "/inputs/base-tests.tar";
    static const char boundary_script[] = "/opt/gate/boundary_selftest.py";
    static const char runner_script[] = "/opt/gate/trusted_runner.py";
    struct runner_result base_result = {0};
    struct runner_result candidate_result = {0};
    uint32_t uid_value;
    uint32_t gid_value;
    bool boundary_ok = false;
    bool base_ok = false;
    bool candidate_ok = false;
    bool gate_ok = false;
    bool setup_ok = true;
    int gate_directory = -1;
    struct sigaction action = {.sa_handler = signal_handler};

    char *extract_candidate[] = {
        "/usr/bin/tar", "--extract", "--file=/inputs/candidate.tar",
        "--directory=/work/candidate-suite", "--no-same-owner",
        "--no-same-permissions", "--delay-directory-restore", NULL,
    };
    char *extract_base_copy[] = {
        "/usr/bin/tar", "--extract", "--file=/inputs/candidate.tar",
        "--directory=/work/base-suite", "--no-same-owner",
        "--no-same-permissions", "--delay-directory-restore", NULL,
    };
    char *remove_base_tests[] = {
        "/usr/bin/rm", "-rf", "--", "/work/base-suite/source/tests", NULL,
    };
    char *overlay_base_tests[] = {
        "/usr/bin/tar", "--extract", "--file=/inputs/base-tests.tar",
        "--directory=/work/base-suite", "--no-same-owner",
        "--no-same-permissions", "--delay-directory-restore", NULL,
    };
    char *boundary[] = {
        "/usr/bin/python3", "-I", "/opt/gate/boundary_selftest.py", NULL,
    };
    char *base_runner[] = {
        "/usr/bin/python3", "-I", "/opt/gate/trusted_runner.py", "--label", "base",
        "--root", "/work/base-suite/source", "--tests",
        "/work/base-suite/source/tests", NULL,
    };
    char *candidate_runner[] = {
        "/usr/bin/python3", "-I", "/opt/gate/trusted_runner.py", "--label",
        "candidate", "--root", "/work/candidate-suite/source", "--tests",
        "/work/candidate-suite/source/tests", NULL,
    };

    if (getpid() != 1 || geteuid() != 0) {
        dprintf(STDERR_FILENO, "trusted-gate: must run as namespace PID 1 and root\n");
        return 125;
    }
    if (ensure_standard_descriptors() < 0)
        return 125;
    if (!parse_id("ALTITUDE_GATE_UID", &uid_value) ||
        !parse_id("ALTITUDE_GATE_GID", &gid_value)) {
        dprintf(STDERR_FILENO, "trusted-gate: invalid ALTITUDE_GATE_UID/GID\n");
        return 125;
    }
    gate_uid = (uid_t)uid_value;
    gate_gid = (gid_t)gid_value;
    if (prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) < 0) {
        dprintf(STDERR_FILENO, "trusted-gate: cannot disable PID 1 dumpability\n");
        return 125;
    }
    if (!secure_root_directory("/gate")) {
        dprintf(STDERR_FILENO, "trusted-gate: /gate is not a trusted directory\n");
        return 125;
    }
    gate_directory = open("/gate", O_RDONLY | O_DIRECTORY | O_CLOEXEC | O_NOFOLLOW);
    if (gate_directory < 0)
        return 125;
    output_fd = open_gate_output(gate_directory, "output.log");
    result_fd = open_gate_output(gate_directory, "result.env");
    close(gate_directory);
    if (output_fd < 0 || result_fd < 0) {
        dprintf(STDERR_FILENO, "trusted-gate: cannot open trusted output files\n");
        return 125;
    }
    log_text("[gate] trusted PID 1 started\n");
    global_deadline_milliseconds = monotonic_milliseconds();
    if (global_deadline_milliseconds < 0) {
        log_text("[gate] cannot establish global deadline\n");
        setup_ok = false;
    } else {
        global_deadline_milliseconds += (int64_t)PID1_GLOBAL_SECONDS * 1000;
    }

    sigemptyset(&action.sa_mask);
    action.sa_flags = 0;
    if (sigaction(SIGTERM, &action, NULL) < 0 ||
        sigaction(SIGINT, &action, NULL) < 0 ||
        sigaction(SIGHUP, &action, NULL) < 0 ||
        prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) < 0) {
        log_text("[gate] signal/reaper setup failed\n");
        setup_ok = false;
    }
    if (setup_ok && !validate_lower_lock()) {
        log_text("[gate] inherited lower-file lock proof failed\n");
        setup_ok = false;
    }

    if (!secure_root_directory("/inputs") || !secure_root_directory("/opt/gate") ||
        !secure_root_directory("/work") || !trusted_regular_file(candidate_archive) ||
        !trusted_regular_file(base_tests_archive) ||
        !trusted_regular_file(boundary_script) || !trusted_regular_file(runner_script) ||
        !trusted_executable("/usr/bin/python3") ||
        !trusted_executable("/usr/bin/tar") || !trusted_executable("/usr/bin/rm")) {
        log_text("[gate] trusted input, tool, or directory validation failed\n");
        setup_ok = false;
    }
    if (setup_ok && make_phase_state("/work/bootstrap-state") < 0) {
        log_text("[gate] bootstrap state directory was not fresh or could not be secured\n");
        setup_ok = false;
    }
    if (setup_ok && make_suite_directory("/work/candidate-suite") < 0) {
        log_text("[gate] candidate work directory was not fresh or could not be secured\n");
        setup_ok = false;
    }

    if (setup_ok && !run_command("extract-candidate", extract_candidate, EXTRACT_CANDIDATE_SECONDS,
                                 "/work/bootstrap-state", NULL, NULL))
        setup_ok = false;
    if (setup_ok && !real_directory("/work/candidate-suite/source")) {
        log_text("[gate] candidate archive lacks a real source/ directory\n");
        setup_ok = false;
    }
    if (setup_ok && make_suite_directory("/work/base-suite") < 0) {
        log_text("[gate] base work directory was not fresh or could not be secured\n");
        setup_ok = false;
    }
    if (setup_ok && !run_command("extract-base-copy", extract_base_copy, EXTRACT_BASE_SECONDS,
                                 "/work/bootstrap-state", NULL, NULL))
        setup_ok = false;
    if (setup_ok && !real_directory("/work/base-suite/source")) {
        log_text("[gate] base copy lacks a real source/ directory\n");
        setup_ok = false;
    }
    if (setup_ok && !run_command("remove-candidate-base-tests", remove_base_tests, REMOVE_TESTS_SECONDS,
                                 "/work/bootstrap-state", NULL, NULL))
        setup_ok = false;
    if (setup_ok && !run_command("overlay-trusted-base-tests", overlay_base_tests, OVERLAY_TESTS_SECONDS,
                                 "/work/bootstrap-state", NULL, NULL))
        setup_ok = false;
    if (setup_ok && !real_directory("/work/base-suite/source/tests")) {
        log_text("[gate] trusted base test archive lacks source/tests/\n");
        setup_ok = false;
    }

    if (setup_ok && (make_phase_state("/work/boundary-state") < 0 ||
                     make_phase_state("/work/base-suite/state") < 0 ||
                     make_phase_state("/work/candidate-suite/state") < 0)) {
        log_text("[gate] per-phase state directories were not fresh or could not be secured\n");
        setup_ok = false;
    }

    if (setup_ok)
        boundary_ok = run_command("boundary-selftest", boundary, BOUNDARY_SECONDS,
                                  "/work/boundary-state", NULL, NULL);
    if (setup_ok && boundary_ok) {
        base_ok = run_command("base-suite", base_runner, BASE_SECONDS,
                              "/work/base-suite/state", "base", &base_result);
        if (!base_ok)
            setup_ok = false;
    }
    if (setup_ok && boundary_ok) {
        candidate_ok = run_command("candidate-suite", candidate_runner, CANDIDATE_SECONDS,
                                   "/work/candidate-suite/state", "candidate",
                                   &candidate_result);
        if (!candidate_ok)
            setup_ok = false;
    }

    gate_ok = setup_ok && boundary_ok && base_ok && candidate_ok && base_result.seen &&
              candidate_result.seen &&
              base_result.successful == 1 && candidate_result.successful == 1 &&
              !terminating && !log_failed && !log_truncated;
    if (fsync(output_fd) < 0)
        gate_ok = false;
    if (write_result(boundary_ok, base_ok, &base_result, candidate_ok,
                     &candidate_result, gate_ok) < 0) {
        log_text("[gate] failed to write result.env\n");
        gate_ok = false;
    }
    close(output_fd);
    close(result_fd);
    if (lower_lock_fd >= 3)
        close(lower_lock_fd);
    return gate_ok ? 0 : 1;
}
