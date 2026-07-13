//! Sandbox environment preparation — copies stdlib and system files into the chroot jail.

use std::path::Path;

use tokio::process::Command;

use crate::config::Config;
use crate::services::LIB_PATH;

/// Copy a source file or directory tree into the sandbox jail via `env.sh`.
async fn copy_into_jail(src: &str) -> Result<(), String> {
    let src_path = Path::new(src);
    if !src_path.exists() {
        tracing::warn!("Sandbox env: path not found, skipping: {src}");
        return Ok(());
    }

    let env_sh = format!("{LIB_PATH}/script/env.sh");
    let child = Command::new("bash")
        .arg(&env_sh)
        .arg(src)
        .arg(LIB_PATH)
        .spawn()
        .map_err(|e| format!("spawn env.sh for {src}: {e}"))?;

    let output = child
        .wait_with_output()
        .await
        .map_err(|e| format!("env.sh {src}: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("env.sh {src} failed: {stderr}"));
    }

    tracing::debug!("Sandbox env: copied {src} -> {LIB_PATH}");
    Ok(())
}

/// Copy configured library paths into the chroot jail. Errors from individual
/// paths are logged and skipped.
pub async fn prepare_environment(config: &Config) {
    tracing::info!(
        python_path_count = config.python_lib_paths.len(),
        nodejs_path_count = config.nodejs_lib_paths.len(),
        "Preparing sandbox environment"
    );
    tracing::debug!(
        python_paths = ?config.python_lib_paths,
        nodejs_paths = ?config.nodejs_lib_paths,
        "Sandbox environment path details"
    );

    for src in &config.python_lib_paths {
        if let Err(e) = copy_into_jail(src).await {
            tracing::warn!("{e}");
        }
    }

    for src in &config.nodejs_lib_paths {
        if let Err(e) = copy_into_jail(src).await {
            tracing::warn!("{e}");
        }
    }

    tracing::info!("Sandbox environment ready");
}
