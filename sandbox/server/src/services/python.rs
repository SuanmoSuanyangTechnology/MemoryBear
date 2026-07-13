use std::process::Stdio;
use std::time::Duration;

use base64::Engine;
use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use super::{ExecutionResult, LIB_PATH};
use crate::config::Config;
use crate::crypto;
use crate::models::RunnerOptions;

/// Template embedded at compile time.
const PYTHON_PRESCRIPT: &str = include_str!("../../../runtime/prescript.py");

/// Build sandbox script and encryption key in memory.
fn build_script(
    config: &Config,
    code: &[u8],
    preload: &str,
    options: &RunnerOptions,
) -> (String, String) {
    let key = crypto::generate_key(64);
    let encoded_key = base64::engine::general_purpose::STANDARD.encode(&key);

    let enable_net = if options.enable_network && config.enable_network {
        1
    } else {
        0
    };

    let checked_preload = if config.enable_preload { preload } else { "" };

    let mut script = PYTHON_PRESCRIPT
        .replace("{{uid}}", &config.sandbox_uid.to_string())
        .replace("{{gid}}", &config.sandbox_gid.to_string())
        .replace("{{enable_network}}", &enable_net.to_string())
        .replace("{{max_as}}", &config.python_max_as_bytes.to_string())
        .replace("{{preload}}", &format!("{checked_preload}\n"));
    let encoded_code = crypto::encrypt_code(code, &key);
    script = script.replace("{{code}}", &encoded_code);

    (script, encoded_key)
}

pub async fn run(
    config: &Config,
    code_b64: &str,
    preload: &str,
    options: &RunnerOptions,
) -> Result<ExecutionResult, String> {
    let code = base64::engine::general_purpose::STANDARD
        .decode(code_b64)
        .map_err(|e| format!("base64 decode: {e}"))?;

    let checked_preload = if config.enable_preload { preload } else { "" };
    let timeout_secs = config.worker_timeout;

    // Fast path: pre-warmed zygote (Unix-only: fork() + seccomp).
    #[cfg(unix)]
    if config.python_zygote && checked_preload.is_empty() {
        if let Some(zygote) = crate::get_zygote() {
            if zygote.is_running() {
                let key = crypto::generate_key(64);
                let enc_code = crypto::encrypt_code(&code, &key);
                let enc_key = base64::engine::general_purpose::STANDARD.encode(&key);
                let net = options.enable_network && config.enable_network;

                let (out, err, exit_code) = zygote
                    .run(
                        &enc_code,
                        &enc_key,
                        config.sandbox_uid,
                        config.sandbox_gid,
                        net,
                        config.python_max_as_bytes,
                        Duration::from_secs(timeout_secs),
                    )
                    .await;

                return Ok(ExecutionResult {
                    stdout: out,
                    stderr: err,
                    exit_code,
                });
            }
            // Zygote died — fall through to the slow path and request a
            // restart. Single-flight inside request_zygote_restart prevents a
            // thundering herd when many concurrent requests see it dead.
            drop(zygote);
            crate::request_zygote_restart(config);
        }
    }

    // Slow path: fresh interpreter via stdin.
    let (script, encoded_key) = build_script(config, &code, checked_preload, options);
    let script_bytes = script.into_bytes();

    let python_path = config.python_path.clone();

    // `python -B -` — feed the script via stdin (no temp file).
    // -B disables .pyc writes: after chroot + privilege drop the sandbox
    // cannot write bytecode caches, avoiding futile openat(O_CREAT) errors.
    // kill_on_drop: on timeout the wait future is dropped; this ensures the
    // child (running untrusted code) is SIGKILLed instead of orphaned.
    let mut cmd = Command::new(&python_path);
    cmd.env_clear();
    // Minimal environment: PATH so the interpreter can find shared libraries.
    if let Ok(path) = std::env::var("PATH") {
        cmd.env("PATH", &path);
    }
    cmd.arg("-B")
        .arg("-")
        .arg(LIB_PATH)
        .arg(&encoded_key)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .current_dir(LIB_PATH)
        .kill_on_drop(true);
    let mut child = cmd
        .spawn()
        .map_err(|e| format!("spawn python: {e}"))?;

    // Write the script to stdin, then close it so Python starts execution.
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(&script_bytes)
            .await
            .map_err(|e| format!("stdin write: {e}"))?;
    }

    let output = tokio::time::timeout(
        Duration::from_secs(timeout_secs),
        child.wait_with_output(),
    )
    .await
    .map_err(|_| "Execution timeout".to_string())?
    .map_err(|e| format!("process: {e}"))?;

    Ok(ExecutionResult {
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        exit_code: super::exit_code_from_status(output.status),
    })
}
