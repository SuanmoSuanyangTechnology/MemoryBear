// src/syscalls.rs

pub static ALLOW_SYSCALLS: &[i32] = &[
    // file io
    libc::SYS_read as i32,
    libc::SYS_write as i32,
    libc::SYS_openat as i32,
    libc::SYS_close as i32,
    libc::SYS_newfstatat as i32,
    libc::SYS_ioctl as i32,
    libc::SYS_lseek as i32,
    libc::SYS_getdents64 as i32,

    // thread
    libc::SYS_futex as i32,

    // memory
    libc::SYS_mmap as i32,
    libc::SYS_brk as i32,
    libc::SYS_mprotect as i32,
    libc::SYS_munmap as i32,
    libc::SYS_rt_sigreturn as i32,
    libc::SYS_mremap as i32,

    // user / group
    libc::SYS_setuid as i32,
    libc::SYS_setgid as i32,
    libc::SYS_getuid as i32,

    // process
    libc::SYS_getpid as i32,
    libc::SYS_getppid as i32,
    libc::SYS_gettid as i32,
    libc::SYS_exit as i32,
    libc::SYS_exit_group as i32,
    libc::SYS_tgkill as i32,
    libc::SYS_rt_sigaction as i32,
    libc::SYS_sched_yield as i32,
    libc::SYS_set_robust_list as i32,
    libc::SYS_get_robust_list as i32,
    libc::SYS_rseq as i32,

    // time
    libc::SYS_clock_gettime as i32,
    libc::SYS_gettimeofday as i32,
    libc::SYS_nanosleep as i32,
    libc::SYS_epoll_create1 as i32,
    libc::SYS_epoll_ctl as i32,
    libc::SYS_clock_nanosleep as i32,
    libc::SYS_pselect6 as i32,
    libc::SYS_rt_sigprocmask as i32,
    libc::SYS_sigaltstack as i32,
    libc::SYS_getrandom as i32,

];

pub static ALLOW_ERROR_SYSCALLS: &[i32] = &[
    libc::SYS_clone as i32,
    libc::SYS_mkdirat as i32,
    libc::SYS_mkdir as i32,
];

pub static ALLOW_NETWORK_SYSCALLS: &[i32] = &[
    libc::SYS_socket as i32,
    libc::SYS_connect as i32,
    libc::SYS_bind as i32,
    libc::SYS_listen as i32,
    libc::SYS_accept as i32,
    libc::SYS_sendto as i32,
    libc::SYS_recvfrom as i32,
    libc::SYS_getsockname as i32,
    libc::SYS_recvmsg as i32,
    libc::SYS_getpeername as i32,
    libc::SYS_setsockopt as i32,
    libc::SYS_ppoll as i32,
    libc::SYS_uname as i32,
    libc::SYS_sendmsg as i32,
    libc::SYS_sendmmsg as i32,
    libc::SYS_getsockopt as i32,
    libc::SYS_fstat as i32,
    libc::SYS_fcntl as i32,
    libc::SYS_fstatfs as i32,
    libc::SYS_poll as i32,
    libc::SYS_epoll_pwait as i32,
];
