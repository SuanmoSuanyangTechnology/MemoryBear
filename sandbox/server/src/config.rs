use serde::Deserialize;
use std::fs;
use std::path::Path;

#[derive(Debug, Clone, Deserialize)]
pub struct AppConfig {
    pub port: u16,
    #[serde(default)]
    #[allow(dead_code)]
    pub debug: bool,
    pub key: String,
}

#[derive(Debug, Clone, Default, Deserialize)]
pub struct ProxyConfig {
    #[serde(default)]
    pub socks5: String,
    #[serde(default)]
    pub http: String,
    #[serde(default)]
    pub https: String,
}

impl ProxyConfig {
    pub fn socks5_option(&self) -> Option<&String> {
        if self.socks5.is_empty() { None } else { Some(&self.socks5) }
    }
    pub fn http_option(&self) -> Option<&String> {
        if self.http.is_empty() { None } else { Some(&self.http) }
    }
    pub fn https_option(&self) -> Option<&String> {
        if self.https.is_empty() { None } else { Some(&self.https) }
    }

    /// Returns the proxy environment variables to inject into a child process.
    /// Socks5 takes precedence: if set, both HTTP and HTTPS use it.
    pub fn proxy_env_vars(&self) -> Vec<(&str, &str)> {
        if let Some(socks5) = self.socks5_option() {
            return vec![("HTTPS_PROXY", socks5.as_str()), ("HTTP_PROXY", socks5.as_str())];
        }
        let mut vars = Vec::new();
        if let Some(h) = self.https_option() {
            vars.push(("HTTPS_PROXY", h.as_str()));
        }
        if let Some(h) = self.http_option() {
            vars.push(("HTTP_PROXY", h.as_str()));
        }
        vars
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    pub app: AppConfig,

    #[serde(default = "default_max_workers")]
    pub max_workers: usize,

    #[serde(default = "default_worker_timeout")]
    pub worker_timeout: u64,

    #[serde(default = "default_true")]
    pub enable_network: bool,

    #[serde(default)]
    pub enable_preload: bool,

    /// When false, runtime pip install and chroot jail setup are skipped
    /// (expected to be baked into the container image at build time).
    /// Non-privileged mode also skips chroot + drop_privileges in the
    /// seccomp sandbox and applies Landlock + O_CREAT filter instead.
    #[serde(default = "default_true")]
    pub privilege: bool,

    /// Use pre-warmed zygote for Python (Linux-only: fork + seccomp).
    #[serde(default)]
    pub python_zygote: bool,

    /// Modules pre-imported in the zygote at startup (inherited via COW).
    #[serde(default = "default_zygote_modules")]
    #[allow(dead_code)]
    pub python_zygote_preload_modules: Vec<String>,

    #[serde(default = "default_python_path")]
    pub python_path: String,

    #[serde(default = "default_nodejs_path")]
    pub nodejs_path: String,

    #[serde(default = "default_python_lib_paths")]
    pub python_lib_paths: Vec<String>,

    #[serde(default = "default_nodejs_lib_paths")]
    pub nodejs_lib_paths: Vec<String>,

    #[serde(default)]
    #[allow(dead_code)]
    pub sandbox_user: String,

    #[serde(default = "default_sandbox_uid")]
    pub sandbox_uid: u32,

    #[serde(default = "default_sandbox_gid")]
    pub sandbox_gid: u32,

    /// Per-request address space cap in bytes (RLIMIT_AS). 0 disables.
    #[serde(default = "default_python_max_as_bytes")]
    pub python_max_as_bytes: u64,

    #[serde(default = "default_nodejs_max_as_bytes")]
    pub nodejs_max_as_bytes: u64,

    /// V8 --max-old-space-size in MB (JS heap cap). Default 768 MiB.
    #[serde(default = "default_nodejs_max_old_space_mb")]
    pub nodejs_max_old_space_mb: u32,

    #[serde(default)]
    pub proxy: ProxyConfig,
}

fn default_max_workers() -> usize { 4 }
fn default_worker_timeout() -> u64 { 30 }
fn default_true() -> bool { true }
fn default_python_path() -> String { "/usr/local/bin/python3".into() }
fn default_nodejs_path() -> String { "/usr/bin/node".into() }
fn default_zygote_modules() -> Vec<String> {
    vec![
        "json", "re", "math", "datetime", "collections", "itertools",
        "functools", "base64", "hashlib", "random", "decimal", "string", "orjson"
    ]
    .into_iter()
    .map(String::from)
    .collect()
}

fn default_sandbox_uid() -> u32 { 65537 }

/// Non-root group. Never 0.
fn default_sandbox_gid() -> u32 { 65537 }

fn default_python_max_as_bytes() -> u64 { 256 * 1024 * 1024 } // 256 MiB
fn default_nodejs_max_as_bytes() -> u64 { 512 * 1024 * 1024 } // 512 MiB (jitless)

fn default_nodejs_max_old_space_mb() -> u32 { 256 }

fn default_python_lib_paths() -> Vec<String> {
    vec![
        "/usr/local/lib/python3.12".into(),
        "/usr/lib/python3".into(),
        "/usr/lib/x86_64-linux-gnu".into(),
        "/etc/ssl/certs/ca-certificates.crt".into(),
        "/etc/nsswitch.conf".into(),
        "/etc/hosts".into(),
        "/etc/resolv.conf".into(),
        "/etc/localtime".into(),
    ]
}

fn default_nodejs_lib_paths() -> Vec<String> {
    vec![
        "/etc/ssl/certs/ca-certificates.crt".into(),
        "/etc/nsswitch.conf".into(),
        "/etc/resolv.conf".into(),
        "/etc/hosts".into(),
    ]
}

impl Config {
    pub fn load(path: impl AsRef<Path>) -> Result<Self, Box<dyn std::error::Error>> {
        let content = fs::read_to_string(path)?;
        let mut cfg: Config = toml::from_str(&content)?;
        cfg.override_with_env();
        Ok(cfg)
    }

    fn override_with_env(&mut self) {
        if let Ok(v) = std::env::var("SANDBOX_PORT") {
            if let Ok(p) = v.parse() { self.app.port = p; }
        }
        if let Ok(v) = std::env::var("MAX_WORKERS") {
            if let Ok(p) = v.parse() { self.max_workers = p; }
        }
        if let Ok(v) = std::env::var("WORKER_TIMEOUT") {
            if let Ok(p) = v.parse() { self.worker_timeout = p; }
        }
        if let Ok(v) = std::env::var("SANDBOX_API_KEY") { self.app.key = v; }
        if let Ok(v) = std::env::var("PYTHON_PATH") { self.python_path = v; }
        if let Ok(v) = std::env::var("NODEJS_PATH") { self.nodejs_path = v; }
        if let Ok(v) = std::env::var("ENABLE_NETWORK") {
            self.enable_network = matches!(v.as_str(), "1" | "true" | "yes");
        }
        if let Ok(v) = std::env::var("ENABLE_PRELOAD") {
            self.enable_preload = matches!(v.as_str(), "1" | "true" | "yes");
        }
        if let Ok(v) = std::env::var("PRIVILEGE") {
            self.privilege = matches!(v.as_str(), "1" | "true" | "yes");
        }
        if let Ok(v) = std::env::var("PYTHON_ZYGOTE") {
            self.python_zygote = matches!(v.as_str(), "1" | "true" | "yes");
        }
        if let Ok(v) = std::env::var("PYTHON_LIB_PATH") {
            self.python_lib_paths = v.split(',').map(|s| s.trim().to_string()).collect();
        }
        if let Ok(v) = std::env::var("NODEJS_LIB_PATH") {
            self.nodejs_lib_paths = v.split(',').map(|s| s.trim().to_string()).collect();
        }
        if let Ok(v) = std::env::var("PYTHON_MAX_AS_BYTES") {
            if let Ok(n) = v.parse() { self.python_max_as_bytes = n; }
        }
        if let Ok(v) = std::env::var("NODEJS_MAX_AS_BYTES") {
            if let Ok(n) = v.parse() { self.nodejs_max_as_bytes = n; }
        }
        if let Ok(v) = std::env::var("NODEJS_MAX_OLD_SPACE_MB") {
            if let Ok(n) = v.parse() { self.nodejs_max_old_space_mb = n; }
        }
        // Backward compat: old env var name
        if let Ok(v) = std::env::var("NODE_MAX_OLD_SPACE_MB") {
            if let Ok(n) = v.parse() { self.nodejs_max_old_space_mb = n; }
        }
        if let Ok(v) = std::env::var("SOCKS5_PROXY") {
            self.proxy.socks5 = v;
        }
        if let Ok(v) = std::env::var("HTTP_PROXY") {
            self.proxy.http = v;
        }
        if let Ok(v) = std::env::var("HTTPS_PROXY") {
            self.proxy.https = v;
        }
    }
}


#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sandbox_gid_default_is_not_root_group() {
        assert_ne!(default_sandbox_gid(), 0, "sandbox must not default to gid 0 (root group)");
        assert_eq!(default_sandbox_gid(), 65537);
    }

    /// A config that omits sandbox_gid must NOT silently fall back to the root
    /// group (the old `#[serde(default)]` gave 0).
    #[test]
    fn omitted_gid_defaults_to_nonroot() {
        let toml = r#"
[app]
port = 8194
key = "test"
"#;
        let cfg: Config = toml::from_str(toml).expect("parse minimal config");
        assert_ne!(cfg.sandbox_gid, 0, "omitted sandbox_gid must not be root group");
        assert_eq!(cfg.sandbox_gid, 65537);
    }

    /// The shipped config.toml must enable zygote by default.
    #[test]
    fn shipped_config_zygote_enabled() {
        let cfg: Config =
            toml::from_str(include_str!("../../runtime/config.toml")).expect("parse shipped config");
        assert!(cfg.python_zygote, "config.toml python_zygote must be true");
    }

    /// The shipped config.toml must not use the root group.
    #[test]
    fn shipped_config_gid_is_not_root() {
        let cfg: Config =
            toml::from_str(include_str!("../../runtime/config.toml")).expect("parse shipped config");
        assert_ne!(cfg.sandbox_gid, 0, "config.toml sandbox_gid must not be 0");
    }
}
