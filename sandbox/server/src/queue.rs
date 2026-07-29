use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Instant;

use tokio::sync::oneshot;

use crate::models::ApiResponse;

type Job = Box<
    dyn FnOnce() -> Pin<Box<dyn Future<Output = ApiResponse> + Send>> + Send + 'static,
>;

pub(crate) struct QueueMessage {
    job: Job,
    respond: oneshot::Sender<ApiResponse>,
    enqueued_at: Instant,
    language: String,
}

#[derive(Debug)]
pub struct QueueStats {
    pub queue_depth: AtomicUsize,
    pub workers: usize,
}

#[derive(Clone)]
pub struct QueueController {
    /// MPMC channel: workers pull directly, no shared Mutex on the receiver.
    sender: async_channel::Sender<QueueMessage>,
    pub stats: Arc<QueueStats>,
}

impl QueueController {
    pub fn start(max_workers: usize) -> Self {
        let (tx, rx) = async_channel::unbounded::<QueueMessage>();
        let stats = Arc::new(QueueStats {
            queue_depth: AtomicUsize::new(0),
            workers: max_workers,
        });

        for worker_id in 0..max_workers {
            let stats = Arc::clone(&stats);
            let rx = rx.clone();
            tokio::spawn(async move {
                while let Ok(msg) = rx.recv().await {
                    let depth = stats.queue_depth.fetch_sub(1, Ordering::Relaxed);
                    let t_exec = Instant::now();
                    let queue_wait_ms = (t_exec - msg.enqueued_at).as_millis();

                    let result = (msg.job)().await;

                    let exec_ms = t_exec.elapsed().as_millis();
                    let total_ms = queue_wait_ms + exec_ms;
                    tracing::info!(
                        "request | worker={:>2} | lang={:<10} | code={:>3} | queue={:>2} | wait={:<5}ms | exec={:<6}ms | total={:<6}ms",
                        worker_id, msg.language, result.code, depth,
                        queue_wait_ms, exec_ms, total_ms,
                    );

                    let _ = msg.respond.send(result);
                }
            });
        }

        Self { sender: tx, stats }
    }

    pub async fn submit<F, Fut>(&self, language: String, f: F) -> ApiResponse
    where
        F: FnOnce() -> Fut + Send + 'static,
        Fut: Future<Output = ApiResponse> + Send + 'static,
    {
        let enqueued_at = Instant::now();
        let (tx, rx) = oneshot::channel();

        self.stats.queue_depth.fetch_add(1, Ordering::Relaxed);

        let job: Job = Box::new(move || Box::pin(f()));
        // Unbounded channel: try_send only fails if all workers are gone.
        if self
            .sender
            .try_send(QueueMessage {
                job,
                respond: tx,
                enqueued_at,
                language,
            })
            .is_err()
        {
            self.stats.queue_depth.fetch_sub(1, Ordering::Relaxed);
            return ApiResponse::error(500, "worker pool unavailable");
        }

        rx.await.unwrap_or_else(|_| ApiResponse::error(500, "worker shut down"))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// MPMC queue dispatches concurrent jobs across workers; queue_depth returns to zero.
    #[tokio::test(flavor = "multi_thread", worker_threads = 4)]
    async fn queue_processes_concurrent_jobs() {
        let q = QueueController::start(4);
        let mut handles = Vec::new();
        for i in 0..50u32 {
            let q = q.clone();
            handles.push(tokio::spawn(async move {
                q.submit("test".into(), move || async move {
                    ApiResponse::success(serde_json::json!({ "i": i }))
                })
                .await
            }));
        }
        for h in handles {
            let r = h.await.unwrap();
            assert_eq!(r.code, 0, "job should succeed");
        }
        assert_eq!(
            q.stats.queue_depth.load(Ordering::Relaxed),
            0,
            "queue_depth should return to zero"
        );
    }
}
