mod config;
mod crypto;
mod handlers;
mod middleware;
mod models;
mod queue;
mod services;
mod setup;

#[cfg(unix)]
use std::sync::{Arc, RwLock};

use actix_web::{web, App, HttpServer};

use crate::config::Config;
use crate::queue::QueueController;
#[cfg(unix)]
use crate::services::LIB_PATH;

/// Global zygote manager — supports auto-restart on connection loss.
/// Only available on Unix (Linux Docker).
#[cfg(unix)]
use crate::services::zygote::ZygoteManager;

#[cfg(unix)]
static ZYGOTE: RwLock<Option<Arc<ZygoteManager>>> = RwLock::new(None);

/// Get a handle to the current zygote manager (if alive).
///
/// Clones the `Arc` out while briefly holding the read lock, then releases
/// the guard. The returned handle is `Send`, so callers can hold it across
/// `.await` points without making their future `!Send`.
#[cfg(unix)]
pub(crate) fn get_zygote() -> Option<Arc<ZygoteManager>> {
    // Recover from a poisoned lock rather than disabling the zygote path: the
    // guarded value is only ever fully replaced, so even a poisoned state
    // still holds a valid Option.
    let guard = ZYGOTE.read().unwrap_or_else(|e| e.into_inner());
    guard.as_ref().cloned()
}

/// Guards against a thundering herd of restarts: only one restart task runs
/// at a time. Many concurrent requests can observe the zygote as dead, but
/// only the first triggers an actual restart.
#[cfg(unix)]
static ZYGOTE_RESTARTING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

/// Try to become the single in-flight restarter. Returns true for exactly one
/// caller until [`end_restart`] is called. Isolated for unit testing.
#[cfg(unix)]
fn try_begin_restart() -> bool {
    use std::sync::atomic::Ordering;
    ZYGOTE_RESTARTING
        .compare_exchange(false, true, Ordering::Acquire, Ordering::Relaxed)
        .is_ok()
}

#[cfg(unix)]
fn end_restart() {
    ZYGOTE_RESTARTING.store(false, std::sync::atomic::Ordering::Release);
}

/// Request a zygote restart in the background (single-flight, non-blocking).
///
/// Safe to call from every request that just saw the zygote die: the atomic
/// gate ensures at most one restart task is in flight, so we never spawn a
/// storm of tasks all contending on the same lock / all spawning interpreters.
#[cfg(unix)]
pub(crate) fn request_zygote_restart(config: &Config) {
    if !try_begin_restart() {
        return; // a restart is already in progress
    }
    let cfg = config.clone();
    tokio::spawn(async move {
        let _ = try_restart_zygote(&cfg).await;
        end_restart();
    });
}

/// Attempt to restart the zygote worker. Called after a connection loss
/// is detected. Returns true if restart succeeded.
///
/// The new interpreter is built WITHOUT holding the `ZYGOTE` lock — spawning a
/// process is slow, and holding a `std` write lock across it would block every
/// other reader/writer (and any tokio worker thread that touches the lock).
/// The write lock is taken only briefly to swap the ready manager in.
#[cfg(unix)]
pub(crate) async fn try_restart_zygote(config: &Config) -> bool {
    // Already back up? (Cheap read-lock check before the expensive spawn.)
    if let Some(z) = get_zygote() {
        if z.is_running() {
            return true;
        }
    }

    // Build the replacement outside any lock.
    let new_z = match ZygoteManager::new(
        &config.python_path,
        "./libpython.so",
        LIB_PATH,
        &config.python_zygote_preload_modules,
        &config.proxy,
    ) {
        Ok(z) => z,
        Err(e) => {
            tracing::error!("Failed to restart zygote: {e}");
            return false;
        }
    };

    // Swap it in under a brief write lock (recovering from poison so a prior
    // panic can't permanently wedge restarts).
    let mut guard = ZYGOTE.write().unwrap_or_else(|e| e.into_inner());
    // A concurrent restart may have already installed a live one.
    if let Some(ref z) = *guard {
        if z.is_running() {
            return true; // `new_z` dropped here (its child is killed)
        }
    }
    tracing::info!("Python zygote pool restarted");
    tracing::debug!(modules = ?config.python_zygote_preload_modules,
                    "Python zygote restarted with modules");
    *guard = Some(Arc::new(new_z));
    true
}

// Non-Unix stub.
#[cfg(not(unix))]
#[allow(dead_code)]
pub(crate) fn get_zygote() -> Option<bool> { None }

#[actix_web::main]
async fn main() -> std::io::Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info".into()),
        )
        .init();

    let config_path =
        std::env::var("CONFIG_PATH").unwrap_or_else(|_| "runtime/config.toml".into());
    let config = Config::load(&config_path).expect("Failed to load config");

    // Hide /proc/<pid>/ from same-UID peers (defense-in-depth).
    unsafe { libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0) };

    let queue = QueueController::start(config.max_workers);

    let port = config.app.port;
    tracing::info!(
        port = port,
        max_workers = config.max_workers,
        worker_timeout = config.worker_timeout,
        "Sandbox server starting"
    );
    tracing::info!(
        python = %config.python_path,
        nodejs = %config.nodejs_path,
        "Runtime paths"
    );
    tracing::info!(
        zygote = config.python_zygote,
        network = config.enable_network,
        preload = config.enable_preload,
        privilege = config.privilege,
        python_max_as_mb = config.python_max_as_bytes / 1048576,
        nodejs_max_as_mb = config.nodejs_max_as_bytes / 1048576,
        "Sandbox flags"
    );

    if config.privilege {
        if let Err(e) = crate::setup::dependencies::install_python_dependencies(&config).await {
            tracing::warn!("Failed to install initial Python dependencies: {e}");
        }

        // Prepare sandbox environment (stdlib, system libs into chroot jail)
        crate::setup::env::prepare_environment(&config).await;
    } else {
        tracing::info!(
            "Non-privileged mode: skipping runtime pip install and chroot jail setup \
             (expected to be baked into the container image)"
        );
    }

    // Unix-only: requires fork() + seccomp.
    #[cfg(unix)]
    if config.python_zygote {
        tracing::info!(
            count = config.python_zygote_preload_modules.len(),
            "Starting Python zygote with preloaded stdlib modules"
        );
        tracing::debug!(
            count = config.python_zygote_preload_modules.len(),
            modules = ?config.python_zygote_preload_modules,
            "Python zygote preload module details"
        );
        match ZygoteManager::new(
            &config.python_path,
            "./libpython.so",
            LIB_PATH,
            &config.python_zygote_preload_modules,
            &config.proxy,
        ) {
            Ok(zygote) => {
                tracing::info!("Python zygote pool started");
                *ZYGOTE.write().unwrap_or_else(|e| e.into_inner()) =
                    Some(Arc::new(zygote));
            }
            Err(e) => {
                tracing::error!("Failed to start Python zygote: {e}");
            }
        }
    } else {
        #[cfg(unix)]
        tracing::info!("Python zygote disabled (set python_zygote=true to enable)");
        #[cfg(not(unix))]
        tracing::info!("Python zygote not available on this platform");
    }

    tracing::info!("Startup complete — Sandbox is ready to accept requests");

    let cfg = config.clone();
    let q = queue.clone();

    HttpServer::new(move || {
        let scope = {
            let s = web::scope("/v1/sandbox")
                .route("/run", web::post().to(handlers::run_code));

            #[cfg(feature = "dependencies-api")]
            let s = s
                .route("/dependencies", web::get().to(handlers::get_dependencies))
                .route(
                    "/dependencies/update",
                    web::post().to(handlers::update_dependencies),
                );

            s
        };

        App::new()
            .app_data(web::Data::new(cfg.clone()))
            .app_data(web::Data::new(q.clone()))
            .route("/health", web::get().to(handlers::health))
            .service(scope)
    })
    .bind(("0.0.0.0", port))?
    .run()
    .await
}


#[cfg(all(test, unix))]
mod restart_gate_tests {
    use super::{end_restart, try_begin_restart};
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;

    #[test]
    fn single_flight_admits_exactly_one() {
        // Ensure a clean gate (other logic never runs in tests).
        end_restart();

        let winners = Arc::new(AtomicUsize::new(0));
        let mut handles = Vec::new();
        for _ in 0..32 {
            let winners = Arc::clone(&winners);
            handles.push(std::thread::spawn(move || {
                if try_begin_restart() {
                    winners.fetch_add(1, Ordering::SeqCst);
                }
            }));
        }
        for h in handles {
            h.join().unwrap();
        }
        assert_eq!(
            winners.load(Ordering::SeqCst),
            1,
            "exactly one caller must win the restart gate"
        );

        // Releasing the gate lets a subsequent restart begin.
        end_restart();
        assert!(try_begin_restart(), "gate should be reusable after release");
        end_restart();
    }
}
