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

/*
 * get_allowed_syscalls - retrieve allowed syscalls for the sandbox
 * @enable_network: enable network-related syscalls if non-zero
 *
 * Syscall selection order:
 *   1. ALLOWED_SYSCALLS environment variable
 *   2. Built-in default allowlist
 *   3. Optional network syscall extension
 *
 * Returns:
 *   (allowed_syscalls, allowed_not_kill_syscalls)
 *     allowed_syscalls: syscalls fully allowed
 *     allowed_not_kill_syscalls: syscalls returning EPERM
 */
pub fn get_allowed_syscalls(enable_network: bool) -> (Vec<i32>, Vec<i32>) {
    let mut allowed_syscalls = Vec::new();
    let mut allowed_not_kill_syscalls = Vec::new();

    /* Syscalls that return error instead of killing */
    allowed_not_kill_syscalls.extend(ALLOW_ERROR_SYSCALLS);

    /* Load from environment variable ALLOWED_SYSCALLS */
    if let Ok(env_val) = env::var("ALLOWED_SYSCALLS") {
        if !env_val.is_empty() {
            for s in env_val.split(',') {
                if let Ok(sc) = i32::from_str(s) {
                    allowed_syscalls.push(sc);
                }
            }
        }
    }

    /* Fallback to default syscalls if env not set */
    if allowed_syscalls.is_empty() {
        allowed_syscalls.extend(ALLOW_SYSCALLS);
        if enable_network {
            allowed_syscalls.extend(ALLOW_NETWORK_SYSCALLS);
        }
    }

    (allowed_syscalls, allowed_not_kill_syscalls)
}

/*
 * setup_root - setup restricted filesystem root
 *
 * Perform chroot(".") and change working directory to "/".
 *
 * Return:
 *   0 on success
 *   negative error code on failure
 */
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

/*
 * set_no_new_privs - enable PR_SET_NO_NEW_PRIVS
 *
 * Prevent privilege escalation via execve.
 *
 * Return:
 *   0 on success
 *   negative error code on failure
 */
fn set_no_new_privs() -> Result<(), c_int> {
    if unsafe { libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) } != 0 {
        return Err(-3);
    }
    Ok(())
}

/*
 * drop_privileges - drop process privileges
 * @uid: target user ID
 * @gid: target group ID
 *
 * Permanently reduce process privileges.
 *
 * Return:
 *   0 on success
 *   negative error code on failure
 */
fn drop_privileges(uid: uid_t, gid: gid_t) -> Result<(), c_int> {
    if unsafe { libc::setgid(gid) } != 0 {
        return Err(-4);
    }
    if unsafe { libc::setuid(uid) } != 0 {
        return Err(-5);
    }
    Ok(())
}

/*
 * install_seccomp - install seccomp filter
 * @enable_network: enable network-related syscalls if non-zero
 *
 * Default action is SCMP_ACT_KILL_PROCESS.
 * Allowed syscalls are explicitly whitelisted.
 *
 * Return:
 *   0 on success
 *   negative error code on failure
 */
fn install_seccomp(enable_network: bool) -> Result<(), c_int> {
    unsafe {
        let ctx = seccomp_init(SCMP_ACT_KILL_PROCESS);
        if ctx.is_null() {
            return Err(-6); /* failed to init seccomp context */
        }

        let (allowed_syscalls, allowed_not_kill_syscalls) = get_allowed_syscalls(enable_network);

        /* add fully allowed syscalls */
        for &sc in &allowed_syscalls {
            if seccomp_rule_add(ctx, SCMP_ACT_ALLOW, sc, 0) != 0 {
                seccomp_release(ctx);
                return Err(-7);
            }
        }

        /* add syscalls returning EPERM */
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

/*
 * init_seccomp - initialize seccomp sandbox
 * @uid: target user ID
 * @gid: target group ID
 * @enable_network: enable network syscalls if non-zero
 *
 * Initialize the sandbox and apply privilege restrictions
 * in the following order:
 *   1. setup_root()
 *   2. set_no_new_privs()
 *   3. drop_privileges()
 *   4. install_seccomp()
 *
 * This function must be called before executing any untrusted code.
 * It is not thread-safe and must be invoked once per process.
 *
 * Return:
 *   0 on success
 *   negative error code on failure
 */
#[unsafe(no_mangle)]
pub unsafe extern "C" fn init_seccomp(uid: uid_t, gid: gid_t, enable_network: i32) -> c_int {
    if let Err(code) = setup_root() {
        return code;
    }
    if let Err(code) = set_no_new_privs() {
        return code;
    }
    if let Err(code) = drop_privileges(uid, gid) {
        return code;
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
