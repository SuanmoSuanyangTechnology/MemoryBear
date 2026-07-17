#[cfg(all(feature = "python3", feature = "nodejs"))]
compile_error!("Only one feature can be enabled: either python3 or nodejs, not both!");

#[cfg(not(any(feature = "python3", feature = "nodejs")))]
compile_error!("You must enable one feature: either python3 or nodejs");

#[cfg(feature = "python3")]
mod python_syscalls;
#[cfg(feature = "python3")]
use crate::python_syscalls::*;

#[cfg(feature = "nodejs")]
mod nodejs_syscalls;
#[cfg(feature = "nodejs")]
use crate::nodejs_syscalls::*;

use libc::{c_char, c_int, chdir, chroot, gid_t, uid_t};
use libseccomp_sys::*;
use std::env;
use std::ffi::CString;
use std::str::FromStr;

// ── Landlock constants (Linux 5.13+) ──

/// Landlock syscall numbers (x86_64).
const SYS_LANDLOCK_CREATE_RULESET: libc::c_long = 444;
const SYS_LANDLOCK_ADD_RULE: libc::c_long = 445;
const SYS_LANDLOCK_RESTRICT_SELF: libc::c_long = 446;

/// Landlock access rights — MUST match the kernel LANDLOCK_ACCESS_FS_* values.
/// ABI v1 (Linux 5.13+): bits 0–12.
/// ABI v2 (Linux 5.19+): bit 13 (REFER).
/// ABI v3 (Linux 6.0+):  bit 14 (TRUNCATE).
/// ABI v4 (Linux 6.2+):  bit 15 (IOCTL_DEV).
const LANDLOCK_ACCESS_FS_EXECUTE: u64 = 1 << 0;
const LANDLOCK_ACCESS_FS_WRITE_FILE: u64 = 1 << 1;
const LANDLOCK_ACCESS_FS_READ_FILE: u64 = 1 << 2;
const LANDLOCK_ACCESS_FS_READ_DIR: u64 = 1 << 3;
const LANDLOCK_ACCESS_FS_REMOVE_DIR: u64 = 1 << 4;
const LANDLOCK_ACCESS_FS_REMOVE_FILE: u64 = 1 << 5;
const LANDLOCK_ACCESS_FS_MAKE_CHAR: u64 = 1 << 6;
const LANDLOCK_ACCESS_FS_MAKE_DIR: u64 = 1 << 7;
const LANDLOCK_ACCESS_FS_MAKE_REG: u64 = 1 << 8;
const LANDLOCK_ACCESS_FS_MAKE_SOCK: u64 = 1 << 9;
const LANDLOCK_ACCESS_FS_MAKE_FIFO: u64 = 1 << 10;
const LANDLOCK_ACCESS_FS_MAKE_BLOCK: u64 = 1 << 11;
const LANDLOCK_ACCESS_FS_MAKE_SYM: u64 = 1 << 12;
const LANDLOCK_ACCESS_FS_REFER: u64 = 1 << 13;
const LANDLOCK_ACCESS_FS_TRUNCATE: u64 = 1 << 14;
const LANDLOCK_ACCESS_FS_IOCTL_DEV: u64 = 1 << 15;

const LANDLOCK_RULE_PATH_BENEATH: u64 = 1;

#[repr(C)]
struct LandlockRulesetAttr {
    handled_access_fs: u64,
}

#[repr(C)]
struct LandlockPathBeneathAttr {
    allowed_access: u64,
    parent_fd: i32,
}

/// Allowed syscalls from env `ALLOWED_SYSCALLS` or built-in defaults.
pub fn get_allowed_syscalls(enable_network: bool) -> (Vec<i32>, Vec<i32>) {
    let mut allowed_syscalls = Vec::new();
    let mut allowed_not_kill_syscalls = Vec::new();

    // Syscalls returning EPERM instead of killing the process.
    allowed_not_kill_syscalls.extend(ALLOW_ERROR_SYSCALLS);

    if let Ok(env_val) = env::var("ALLOWED_SYSCALLS") {
        if !env_val.is_empty() {
            for s in env_val.split(',') {
                if let Ok(sc) = i32::from_str(s) {
                    allowed_syscalls.push(sc);
                }
            }
        }
    }

    // Fallback to built-in defaults.
    if allowed_syscalls.is_empty() {
        allowed_syscalls.extend(ALLOW_SYSCALLS);
        if enable_network {
            allowed_syscalls.extend(ALLOW_NETWORK_SYSCALLS);
        }
    }

    (allowed_syscalls, allowed_not_kill_syscalls)
}

fn setup_root() -> Result<(), c_int> {
    let root = CString::new(".").unwrap();
    if unsafe { chroot(root.as_ptr()) } != 0 {
        return Err(-1);
    }

    let root_dir = CString::new("/").unwrap();
    if unsafe { chdir(root_dir.as_ptr()) } != 0 {
        return Err(-2);
    }

    Ok(())
}

/// Prevent privilege escalation via execve.
fn set_no_new_privs() -> Result<(), c_int> {
    if unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) } != 0 {
        return Err(-3);
    }
    Ok(())
}

/// Drop ALL supplementary groups, then setgid/setuid.
/// Must call setgroups() first to clear inherited root-group memberships.
fn drop_privileges(uid: uid_t, gid: gid_t) -> Result<(), c_int> {
    // Clear supplementary groups while still privileged (needs CAP_SETGID).
    if unsafe { libc::setgroups(0, std::ptr::null()) } != 0 {
        return Err(-10);
    }
    if unsafe { libc::setgid(gid) } != 0 {
        return Err(-4);
    }
    if unsafe { libc::setuid(uid) } != 0 {
        return Err(-5);
    }
    Ok(())
}

/// Cap virtual address space (RLIMIT_AS). 0 disables.
fn set_memory_limit(max_as_bytes: u64) -> Result<(), c_int> {
    if max_as_bytes == 0 {
        return Ok(());
    }
    let lim = libc::rlimit {
        rlim_cur: max_as_bytes as libc::rlim_t,
        rlim_max: max_as_bytes as libc::rlim_t,
    };
    if unsafe { libc::setrlimit(libc::RLIMIT_AS, &lim) } != 0 {
        return Err(-11);
    }
    Ok(())
}

/// Apply Landlock filesystem restrictions to emulate chroot-like isolation.
///
/// `paths` is a NULL-terminated array of C strings.  The calling process will
/// be restricted to read + execute access within each listed directory tree.
/// Requires Linux 5.13+. Returns 0 on success, negative on error.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn apply_landlock(paths: *const *const c_char) -> c_int {
    if paths.is_null() {
        return -20;
    }

    let handled_access = LANDLOCK_ACCESS_FS_EXECUTE
        | LANDLOCK_ACCESS_FS_WRITE_FILE
        | LANDLOCK_ACCESS_FS_READ_FILE
        | LANDLOCK_ACCESS_FS_READ_DIR
        | LANDLOCK_ACCESS_FS_REMOVE_DIR
        | LANDLOCK_ACCESS_FS_REMOVE_FILE
        | LANDLOCK_ACCESS_FS_MAKE_CHAR
        | LANDLOCK_ACCESS_FS_MAKE_DIR
        | LANDLOCK_ACCESS_FS_MAKE_REG
        | LANDLOCK_ACCESS_FS_MAKE_SOCK
        | LANDLOCK_ACCESS_FS_MAKE_FIFO
        | LANDLOCK_ACCESS_FS_MAKE_BLOCK
        | LANDLOCK_ACCESS_FS_MAKE_SYM
        | LANDLOCK_ACCESS_FS_REFER
        | LANDLOCK_ACCESS_FS_TRUNCATE
        | LANDLOCK_ACCESS_FS_IOCTL_DEV;

    let ruleset_attr = LandlockRulesetAttr {
        handled_access_fs: handled_access,
    };

    let ruleset_fd = libc::syscall(
        SYS_LANDLOCK_CREATE_RULESET,
        &ruleset_attr as *const _,
        core::mem::size_of::<LandlockRulesetAttr>(),
        0u32,
    ) as i32;
    if ruleset_fd < 0 {
        // Kernel may reject unknown bits — retry with only ABI v1 bits.
        let handled_v1 = LANDLOCK_ACCESS_FS_EXECUTE
            | LANDLOCK_ACCESS_FS_WRITE_FILE
            | LANDLOCK_ACCESS_FS_READ_FILE
            | LANDLOCK_ACCESS_FS_READ_DIR
            | LANDLOCK_ACCESS_FS_REMOVE_DIR
            | LANDLOCK_ACCESS_FS_REMOVE_FILE
            | LANDLOCK_ACCESS_FS_MAKE_CHAR
            | LANDLOCK_ACCESS_FS_MAKE_DIR
            | LANDLOCK_ACCESS_FS_MAKE_REG
            | LANDLOCK_ACCESS_FS_MAKE_SOCK
            | LANDLOCK_ACCESS_FS_MAKE_FIFO
            | LANDLOCK_ACCESS_FS_MAKE_BLOCK
            | LANDLOCK_ACCESS_FS_MAKE_SYM;
        let ruleset_attr = LandlockRulesetAttr { handled_access_fs: handled_v1 };
        let ruleset_fd = libc::syscall(
            SYS_LANDLOCK_CREATE_RULESET,
            &ruleset_attr as *const _,
            core::mem::size_of::<LandlockRulesetAttr>(),
            0u32,
        ) as i32;
        if ruleset_fd < 0 {
            return -21;
        }
    }

    let allowed_access = LANDLOCK_ACCESS_FS_EXECUTE
        | LANDLOCK_ACCESS_FS_READ_FILE
        | LANDLOCK_ACCESS_FS_READ_DIR;

    // Add a path_beneath rule for each path in the NULL-terminated array.
    let mut p = paths;
    let mut added = 0u32;
    while !(*p).is_null() {
        let path = *p;
        let dir_fd = libc::open(path, libc::O_PATH | libc::O_CLOEXEC);
        if dir_fd >= 0 {
            let path_beneath = LandlockPathBeneathAttr {
                allowed_access,
                parent_fd: dir_fd,
            };
            let ret = libc::syscall(
                SYS_LANDLOCK_ADD_RULE,
                ruleset_fd as i64,
                LANDLOCK_RULE_PATH_BENEATH as i64,
                &path_beneath as *const _,
                0u32,
            );
            libc::close(dir_fd);
            if ret == 0 {
                added += 1;
            }
        }
        p = p.add(1);
    }
    // If no rules were added (all paths failed), the sandbox would be empty.
    if added == 0 {
        libc::close(ruleset_fd);
        return -22;
    }

    // landlock_restrict_self requires no_new_privs already set.
    if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0 {
        libc::close(ruleset_fd);
        return -24;
    }

    let ret = libc::syscall(
        SYS_LANDLOCK_RESTRICT_SELF,
        ruleset_fd as i64,
        0u32,
    );
    libc::close(ruleset_fd);

    if ret != 0 {
        return -23;
    }
    0
}

/// Convenience wrapper: apply Landlock for a single path.
/// Equivalent to `apply_landlock(&[lib_path, NULL])`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn apply_landlock_one(lib_path: *const c_char) -> c_int {
    let paths: [*const c_char; 2] = [lib_path, std::ptr::null()];
    apply_landlock(paths.as_ptr())
}

/// Install seccomp filter. Default action: SCMP_ACT_KILL_PROCESS.
///
/// `openat` is only allowed when the `O_CREAT` flag is **not** set, preventing
/// the sandboxed process from creating new files.  If `O_CREAT` is present the
/// call falls through to the default `KILL_PROCESS`.
fn install_seccomp(enable_network: bool) -> Result<(), c_int> {
    unsafe {
        let ctx = seccomp_init(SCMP_ACT_KILL_PROCESS);
        if ctx.is_null() {
            return Err(-6);
        }

        let (allowed_syscalls, allowed_not_kill_syscalls) = get_allowed_syscalls(enable_network);

        for &sc in &allowed_syscalls {
            if sc == libc::SYS_openat as i32 {
                // Allow openat only when O_CREAT is NOT set in flags (arg idx 2).
                let cmp = scmp_arg_cmp {
                    arg: 2,
                    op: scmp_compare::SCMP_CMP_MASKED_EQ,
                    datum_a: 0,                         // O_CREAT bit must be 0
                    datum_b: libc::O_CREAT as u64,      // mask
                };
                if seccomp_rule_add_array(ctx, SCMP_ACT_ALLOW, sc, 1, &cmp) != 0 {
                    seccomp_release(ctx);
                    return Err(-7);
                }
            } else {
                if seccomp_rule_add(ctx, SCMP_ACT_ALLOW, sc, 0) != 0 {
                    seccomp_release(ctx);
                    return Err(-7);
                }
            }
        }

        for &sc in &allowed_not_kill_syscalls {
            if seccomp_rule_add(ctx, SCMP_ACT_ERRNO(libc::EPERM as u16), sc, 0) != 0 {
                seccomp_release(ctx);
                return Err(-8);
            }
        }

        if seccomp_load(ctx) != 0 {
            seccomp_release(ctx);
            return Err(-9);
        }

        seccomp_release(ctx);
        Ok(())
    }
}

/// Initialize sandbox.
///
/// Privileged mode (`privilege != 0`):
///   RLIMIT_AS → chroot → no_new_privs → drop_privs → seccomp
///
/// Non-privileged mode (`privilege == 0`):
///   RLIMIT_AS → (Landlock applied separately by caller) → no_new_privs
///   → seccomp
///
/// In both modes, `openat` is only allowed when `O_CREAT` is not set.
/// Must be called once per process before executing untrusted code.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn init_seccomp(
    uid: uid_t,
    gid: gid_t,
    enable_network: i32,
    max_as_bytes: u64,
    privilege: i32,
) -> c_int {
    if let Err(code) = set_memory_limit(max_as_bytes) {
        return code;
    }
    if privilege != 0 {
        if let Err(code) = setup_root() {
            return code;
        }
    }
    if let Err(code) = set_no_new_privs() {
        return code;
    }
    if privilege != 0 {
        if let Err(code) = drop_privileges(uid, gid) {
            return code;
        }
    }
    match install_seccomp(enable_network != 0) {
        Ok(_) => 0,
        Err(code) => code,
    }
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn get_lib_version_static() -> *const c_char {
    concat!(env!("CARGO_PKG_VERSION"), "\0").as_ptr() as *const c_char
}

#[unsafe(no_mangle)]
pub unsafe extern "C" fn get_lib_feature_static() -> *const c_char {
    #[cfg(feature = "python3")]
    let s = b"python3\0";
    #[cfg(feature = "nodejs")]
    let s = b"nodejs\0";
    #[cfg(not(any(feature = "python3", feature = "nodejs")))]
    let s = b"none\0";

    s.as_ptr() as *const c_char
}


#[cfg(test)]
mod tests {
    use super::*;

    /// A cap of 0 means "disabled" and must be a no-op (no setrlimit call),
    /// so it can never fail. (Non-zero values would alter the test process's
    /// own RLIMIT_AS, so we don't exercise them here.)
    #[test]
    fn zero_cap_is_disabled_noop() {
        assert!(set_memory_limit(0).is_ok());
    }
}
