use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Deserialize)]
pub struct RunCodeRequest {
    pub language: String,
    pub code: String,
    #[serde(default)]
    pub preload: String,
    #[serde(default)]
    pub options: RunnerOptions,
}

#[derive(Debug, Clone, Deserialize, Default)]
pub struct RunnerOptions {
    /// Enable network access for this execution. Defaults to `false` so
    /// callers must opt in per-request (matched with `config.enable_network`).
    #[serde(default)]
    pub enable_network: bool,
}

#[derive(Debug, Clone, Deserialize)]
#[cfg_attr(not(feature = "dependencies-api"), allow(dead_code))]
pub struct UpdateDependencyRequest {
    pub language: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct ApiResponse {
    pub code: i32,
    pub message: String,
    pub data: Option<serde_json::Value>,
}

#[derive(Debug, Clone, Serialize)]
pub struct RunCodeData {
    pub stdout: String,
    pub stderr: String,
}

#[derive(Debug, Clone, Serialize)]
#[cfg_attr(not(feature = "dependencies-api"), allow(dead_code))]
pub struct Dependency {
    pub name: String,
    pub version: String,
}

impl ApiResponse {
    pub fn success<T: Serialize>(data: T) -> Self {
        Self {
            code: 0,
            message: "success".into(),
            data: Some(serde_json::to_value(data).unwrap_or_default()),
        }
    }

    pub fn error(code: i32, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
            data: None,
        }
    }
}

