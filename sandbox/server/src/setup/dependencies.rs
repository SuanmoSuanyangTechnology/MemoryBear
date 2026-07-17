//! Dependency management — pip list, pip install via subprocess.

use std::process::Stdio;

use tokio::process::Command;

use crate::config::Config;
use crate::services::LIB_PATH;

#[derive(Debug, serde::Serialize)]
#[cfg_attr(not(feature = "dependencies-api"), allow(dead_code))]
pub struct Package {
    pub name: String,
    pub version: String,
}

/// List installed Python packages via `pip list --format=freeze`.
#[cfg(feature = "dependencies-api")]
pub async fn list_python_packages(config: &Config) -> Vec<Package> {
    let result = Command::new(&config.python_path)
        .args(["-m", "pip", "list", "--format=freeze"])
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn();

    let Ok(child) = result else { return vec![] };

    let output = match child.wait_with_output().await {
        Ok(o) => o,
        Err(_) => return vec![],
    };

    if !output.status.success() {
        return vec![];
    }

    String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|line| {
            let line = line.trim();
            line.split_once("==")
                .map(|(n, v)| Package {
                    name: n.to_string(),
                    version: v.to_string(),
                })
        })
        .collect()
}

/// Install Python packages from requirements file.
pub async fn install_python_dependencies(config: &Config) -> Result<(), String> {
    let req_path = format!("{}/requirements.txt", LIB_PATH);
    let requirements = match std::fs::read_to_string(req_path) {
        Ok(r) => r,
        Err(_) => {
            tracing::warn!("Python requirements file not found, skipping");
            return Ok(());
        }
    };

    let packages: Vec<&str> = requirements
        .lines()
        .map(str::trim)
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
        .collect();

    if packages.is_empty() {
        return Ok(());
    }

    let mut cmd = Command::new(&config.python_path);
    cmd.args(["-m", "pip", "install", "--upgrade"]);
    for pkg in &packages {
        cmd.arg(pkg);
    }
    cmd.stdout(Stdio::piped()).stderr(Stdio::piped());

    let child = cmd.spawn().map_err(|e| format!("pip install spawn: {e}"))?;
    let output = child.wait_with_output().await.map_err(|e| format!("pip install: {e}"))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        tracing::error!(%stderr, "pip install failed");
        return Err(stderr.into_owned());
    }

    tracing::info!(count = packages.len(), "Python dependencies installed successfully");
    Ok(())
}
