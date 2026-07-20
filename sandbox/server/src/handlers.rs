use actix_web::{web, HttpResponse};
use std::time::Instant;

use crate::config::Config;
use crate::middleware::ApiKey;
use crate::models::{ApiResponse, RunCodeData, RunCodeRequest, RunnerOptions};
use crate::queue::QueueController;
use crate::services;
#[cfg(feature = "dependencies-api")]
use crate::models::{Dependency, UpdateDependencyRequest};
#[cfg(feature = "dependencies-api")]
use crate::setup::dependencies;

pub async fn health(queue: web::Data<QueueController>) -> HttpResponse {
    let stats = &queue.stats;
    HttpResponse::Ok().json(serde_json::json!({
        "ok": true,
        "role": "sandbox",
        "queue_depth": stats.queue_depth.load(std::sync::atomic::Ordering::Relaxed),
        "workers": stats.workers,
    }))
}

pub async fn run_code(
    req: web::Json<RunCodeRequest>,
    config: web::Data<Config>,
    queue: web::Data<QueueController>,
    _api_key: ApiKey,
) -> HttpResponse {
    let t_enqueue = Instant::now();
    let req = req.into_inner();
    let cfg = config.clone();
    let language = req.language.clone();

    let result = queue
        .submit(language.clone(), move || {
            async move {
                match req.language.as_str() {
                    "python3" => run_python(&cfg, &req.code, &req.preload, &req.options).await,
                    "javascript" => run_nodejs(&cfg, &req.code, &req.preload, &req.options).await,
                    _ => ApiResponse::error(400, "unsupported language"),
                }
            }
        })
        .await;

    let total_ms = t_enqueue.elapsed().as_millis();
    tracing::debug!(total_ms = total_ms, "request completed");

    HttpResponse::Ok().json(result)
}

#[cfg(feature = "dependencies-api")]
pub async fn get_dependencies(
    query: web::Query<std::collections::HashMap<String, String>>,
    config: web::Data<Config>,
    _api_key: ApiKey,
) -> HttpResponse {
    match query.get("language").map(String::as_str) {
        Some("python3") => {
            let packages = dependencies::list_python_packages(&config).await;
            let deps: Vec<Dependency> = packages
                .into_iter()
                .map(|p| Dependency { name: p.name, version: p.version })
                .collect();
            HttpResponse::Ok().json(ApiResponse::success(serde_json::json!({ "dependencies": deps })))
        }
        _ => HttpResponse::Ok().json(ApiResponse::error(400, "unsupported language")),
    }
}

#[cfg(feature = "dependencies-api")]
pub async fn update_dependencies(
    req: web::Json<UpdateDependencyRequest>,
    config: web::Data<Config>,
    _api_key: ApiKey,
) -> HttpResponse {
    match req.language.as_str() {
        "python3" => {
            match dependencies::install_python_dependencies(&config).await {
                Ok(()) => HttpResponse::Ok().json(ApiResponse::success(serde_json::json!({"success": true}))),
                Err(e) => HttpResponse::Ok().json(ApiResponse::error(500, e)),
            }
        }
        _ => HttpResponse::Ok().json(ApiResponse::error(400, "unsupported language")),
    }
}

async fn run_python(
    config: &Config,
    code: &str,
    preload: &str,
    options: &RunnerOptions,
) -> ApiResponse {
    match services::python::run(config, code, preload, options).await {
        Ok(result) => {
            if services::is_seccomp_violation(result.exit_code) {
                return ApiResponse::error(31, "sandbox security policy violation");
            }
            if !result.stderr.is_empty() && result.exit_code != 0 {
                return ApiResponse::error(500, result.stderr);
            }
            ApiResponse::success(RunCodeData {
                stdout: result.stdout,
                stderr: result.stderr,
            })
        }
        Err(e) => ApiResponse::error(500, e),
    }
}

async fn run_nodejs(
    config: &Config,
    code: &str,
    preload: &str,
    options: &RunnerOptions,
) -> ApiResponse {
    match services::nodejs::run(config, code, preload, options).await {
        Ok(result) => {
            if services::is_seccomp_violation(result.exit_code) {
                return ApiResponse::error(31, "sandbox security policy violation");
            }
            if !result.stderr.is_empty() && result.exit_code != 0 {
                return ApiResponse::error(500, result.stderr);
            }
            ApiResponse::success(RunCodeData {
                stdout: result.stdout,
                stderr: result.stderr,
            })
        }
        Err(e) => ApiResponse::error(500, e),
    }
}
