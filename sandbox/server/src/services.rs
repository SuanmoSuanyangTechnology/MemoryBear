pub mod nodejs;
pub mod python;
pub mod zygote;

use std::process::ExitStatus;

/// Result of a code execution.
pub struct ExecutionResult {
    pub stdout: String,
    pub stderr: String,
    pub exit_code: i32,
}

/// Path where the seccomp library, prescript, and temp dirs live.
pub const LIB_PATH: &str = "/usr/local/share/sandbox";

/// Convert an [`ExitStatus`] into an exit code that matches Python's
/// `process.returncode` convention: normal exit → exit code (0–255),
/// signal death → `-signal_number` (e.g. SIGSYS=31 → -31).
///
/// On non-Unix platforms falls back to `code().unwrap_or(-1)`.
pub fn exit_code_from_status(status: ExitStatus) -> i32 {
    #[cfg(unix)]
    {
        use std::os::unix::process::ExitStatusExt;
        if let Some(signal) = status.signal() {
            return -signal;
        }
    }
    status.code().unwrap_or(-1)
}

// SIGSYS sentinel values (signal 31).
/// SIGSYS = signal 31; raw wait status = 128 + 31 = 159; negative-signal = -31.
pub(crate) const SIGSYS_RAW: i32 = 159;
pub(crate) const SIGSYS_NEG: i32 = -31;

pub(crate) fn is_seccomp_violation(exit_code: i32) -> bool {
    exit_code == SIGSYS_RAW || exit_code == SIGSYS_NEG
}

#[cfg(all(test, unix))]
mod tests {
    use std::time::Duration;
    use tokio::process::Command;

    /// Read the process state char from `/proc/<pid>/stat`.
    /// Returns `None` if the process no longer exists (reaped).
    /// `'Z'` means a reaped-pending zombie (already dead, not running).
    fn proc_state(pid: i32) -> Option<char> {
        let s = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
        let rparen = s.rfind(')')?;
        s[rparen + 1..].trim_start().chars().next()
    }

    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn timeout_kills_child() {
        let child = Command::new("sleep")
            .arg("30")
            .kill_on_drop(true)
            .spawn()
            .expect("spawn sleep");
        let pid = child.id().expect("pid") as i32;
        assert!(
            matches!(proc_state(pid), Some(c) if c != 'Z'),
            "sleep should be alive before timeout"
        );

        let res = tokio::time::timeout(Duration::from_millis(100), child.wait_with_output()).await;
        assert!(res.is_err(), "expected timeout to elapse");

        let mut terminated = false;
        for _ in 0..100 {
            match proc_state(pid) {
                None | Some('Z') => {
                    terminated = true;
                    break;
                }
                _ => tokio::time::sleep(Duration::from_millis(20)).await,
            }
        }
        assert!(terminated, "child pid {pid} survived timeout — H2 regression");
    }
}
