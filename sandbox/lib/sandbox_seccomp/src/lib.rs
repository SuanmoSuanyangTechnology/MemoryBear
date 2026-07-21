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
use std::ffi::{CStr, CString};
use std::str::FromStr;

// ── Landlock (safe abstraction via landlock crate) ──

use landlock::{ABI, Access, AccessFs, Ruleset, RulesetAttr, RulesetCreatedAttr, RulesetStatus, Scope, path_beneath_rules};

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
    // Prevent other processes (even same-UID) from reading /proc/<pid>/ files
    // (maps, fd, environ, …).  The kernel already rejects non-owner access, but
    // PR_SET_DUMPABLE=0 additionally hides the process from same-UID peers.
    if unsafe { libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0) } != 0 {
        return Err(-12);
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
        let err = unsafe { *libc::__errno_location() };
        eprintln!("set_memory_limit({max_as_bytes}) failed: errno={err}");
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

    // Convert NULL-terminated C string array to Rust &str slice.
    let path_strs: Vec<&str> = unsafe {
        let mut p = paths;
        let mut v = Vec::new();
        while !(*p).is_null() {
            if let Ok(s) = CStr::from_ptr(*p).to_str() {
                v.push(s);
            }
            p = p.add(1);
        }
        v
    };

    if path_strs.is_empty() {
        return -20;
    }

    let abi = ABI::V6;
    let status = Ruleset::default()
        .handle_access(AccessFs::from_all(abi))
        .and_then(|r| r.scope(Scope::Signal))
        .and_then(|r| r.create())
        .and_then(|r| r.add_rules(path_beneath_rules(&path_strs, AccessFs::from_read(abi))))
        .and_then(|r| r.restrict_self());

    match status {
        Ok(s) => match s.ruleset {
            RulesetStatus::FullyEnforced | RulesetStatus::PartiallyEnforced => 0,
            RulesetStatus::NotEnforced => -23,
        },
        Err(_) => -21,
    }
}

/// Convenience wrapper: apply Landlock for a single path.
/// Equivalent to `apply_landlock(&[lib_path, NULL])`.
#[unsafe(no_mangle)]
pub unsafe extern "C" fn apply_landlock_one(lib_path: *const c_char) -> c_int {
    let paths: [*const c_char; 2] = [lib_path, std::ptr::null()];
    unsafe { apply_landlock(paths.as_ptr()) }
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
