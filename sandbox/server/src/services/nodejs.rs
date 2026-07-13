use std::process::Stdio;
use std::time::Duration;

use tokio::io::AsyncWriteExt;
use tokio::process::Command;

use super::{ExecutionResult, LIB_PATH};
use crate::config::Config;
use crate::models::RunnerOptions;

const NODEJS_PRESCRIPT: &str = include_str!("../../../runtime/prescript.js");

/// Validate base64 and return canonical re-encoding (closes JS string-injection surface).
fn validate_base64(code_b64: &str) -> Result<String, String> {
    use base64::Engine;
    let decoded = base64::engine::general_purpose::STANDARD
        .decode(code_b64)
        .map_err(|e| format!("invalid base64 code: {e}"))?;
    Ok(base64::engine::general_purpose::STANDARD.encode(decoded))
}

/// Build sandbox script in memory. `code_b64` must already be validated.
fn build_script(
    code_b64: &str,
    preload: &str,
) -> String {
    let mut script = NODEJS_PRESCRIPT.replace("{{preload}}", preload);
    let eval_code = format!(
        "eval(Buffer.from('{}', 'base64').toString('utf-8'))",
        code_b64
    );
    script = script.replace("{{code}}", &eval_code);
    script
}

pub async fn run(
    config: &Config,
    code_b64: &str,
    preload: &str,
    options: &RunnerOptions,
) -> Result<ExecutionResult, String> {
    let checked_preload = if config.enable_preload { preload } else { "" };
    let enable_network = options.enable_network && config.enable_network;
    let timeout_secs = config.worker_timeout;

    // Validate the incoming code is well-formed base64 BEFORE it is spliced
    // into the JS template — consistent with the Python runner and closing the
    // string-injection surface in build_script.
    let code_b64 = validate_base64(code_b64)?;

    let script = build_script(&code_b64, checked_preload);
    let script_bytes = script.into_bytes();

    let nodejs_path = config.nodejs_path.clone();
    let sandbox_uid = config.sandbox_uid;
    let sandbox_gid = config.sandbox_gid;

    // Build options JSON string for the prescript
    let opts_json = format!(
        r#"{{"enable_network":{},"max_as":{}}}"#,
        enable_network, config.nodejs_max_as_bytes
    );

    // `node -` — feed the script via stdin (no temp file).
    // The interpreter reads and compiles the whole program before the
    // embedded init_seccomp() applies chroot/seccomp.
    let mut cmd = Command::new(&nodejs_path);
    cmd.env_clear();
    // Minimal environment — only what the interpreter needs.
    if let Ok(path) = std::env::var("PATH") {
        cmd.env("PATH", &path);
    }
    cmd.env("UV_USE_IO_URING", "0");
    // Point NODE_PATH at the bundled node_modules so require('koffi')
    // resolves before init_seccomp() applies chroot.
    cmd.env("NODE_PATH", format!("{LIB_PATH}/node_modules"));
    if let Some(socks5) = config.proxy.socks5_option() {
        cmd.env("HTTPS_PROXY", socks5).env("HTTP_PROXY", socks5);
    } else {
        if let Some(h) = config.proxy.https_option() {
            cmd.env("HTTPS_PROXY", h);
        }
        if let Some(h) = config.proxy.http_option() {
            cmd.env("HTTP_PROXY", h);
        }
    }

    // Cap V8's JS heap so JS-object bombs die with a clean heap-OOM below the
    // RLIMIT_AS ceiling. Override with NODE_MAX_OLD_SPACE_MB.
    let old_space_mb = std::env::var("NODE_MAX_OLD_SPACE_MB")
        .ok()
        .and_then(|v| v.trim().parse::<u32>().ok())
        .filter(|&n| n > 0)
        .unwrap_or(768);
    cmd.arg(format!("--max-old-space-size={old_space_mb}"))
        .arg("-")
        .arg(LIB_PATH)
        .arg(sandbox_uid.to_string())
        .arg(sandbox_gid.to_string())
        .arg(&opts_json)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .current_dir(LIB_PATH)
        // On timeout the wait future is dropped; kill_on_drop guarantees the
        // child (running untrusted code) is SIGKILLed rather than orphaned.
        .kill_on_drop(true);

    let mut child = cmd
        .spawn()
        .map_err(|e| format!("spawn nodejs: {e}"))?;

    // Write the script to stdin, then close it so Node.js starts execution.
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



#[cfg(test)]
mod tests {
    use super::*;
    use base64::Engine;

    fn b64(s: &str) -> String {
        base64::engine::general_purpose::STANDARD.encode(s)
    }

    /// C1 injection surface: if raw (unvalidated) input reached `build_script`,
    /// a payload containing a quote would break out of the JS string literal.
    /// This documents exactly what `validate_base64` prevents from ever
    /// arriving at `build_script`.
    #[test]
    fn build_script_splices_raw_code_verbatim() {
        let payload = "'); globalThis.PWNED=1; //";
        let script = build_script(payload, "");
        // The quote is spliced verbatim -> broken-out injection.
        assert!(script.contains("globalThis.PWNED=1"));
        assert!(script.contains("eval(Buffer.from('');"));
    }

    /// The classic breakout PoC is not valid base64, so validation rejects it
    /// before it can reach `build_script`.
    #[test]
    fn validate_rejects_injection_payload() {
        let payload = "'); require('child_process').execSync('id'); //";
        assert!(validate_base64(payload).is_err());
    }

    /// Any quote/backslash/newline breaks the base64 alphabet -> rejected.
    #[test]
    fn validate_rejects_quote_and_control_chars() {
        assert!(validate_base64("abc'def").is_err());
        assert!(validate_base64("abc\\def").is_err());
        assert!(validate_base64("abc\ndef").is_err());
    }

    /// Legit base64 is accepted and round-trips to the original source.
    #[test]
    fn validate_accepts_valid_base64() {
        let src = "console.log('hi')";
        let canonical = validate_base64(&b64(src)).expect("valid base64");
        let back = base64::engine::general_purpose::STANDARD
            .decode(&canonical)
            .unwrap();
        assert_eq!(back, src.as_bytes());
    }

    /// After validation, whatever `build_script` embeds is pure base64
    /// alphabet, so the resulting eval wrapper is well-formed and unbreakable.
    #[test]
    fn validated_code_produces_safe_wrapper() {
        let canonical = validate_base64(&b64("1+1")).unwrap();
        let script = build_script(&canonical, "");
        let expected = format!("eval(Buffer.from('{canonical}', 'base64').toString('utf-8'))");
        assert!(script.contains(&expected));
        // No stray quote could have been introduced.
        assert!(!canonical.contains('\''));
    }
}
