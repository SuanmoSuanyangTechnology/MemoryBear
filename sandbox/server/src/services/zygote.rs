//! Pre-warmed Python zygote pool — avoids per-request interpreter cold start.

use std::collections::HashMap;
use std::io;
use std::os::fd::FromRawFd;
use std::os::unix::net::UnixStream as StdUnixStream;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::UnixStream;
use tokio::net::unix::{OwnedReadHalf, OwnedWriteHalf};
use tokio::sync::{Mutex, oneshot};
use tokio::task::JoinHandle;

use crate::services::LIB_PATH;

fn socketpair() -> io::Result<(i32, i32)> {
    let mut fds = [-1i32; 2];
    let rc = unsafe { libc::socketpair(libc::AF_UNIX, libc::SOCK_STREAM, 0, fds.as_mut_ptr()) };
    if rc != 0 {
        return Err(io::Error::last_os_error());
    }
    Ok((fds[0], fds[1]))
}

// Wire protocol (shared with pool/protocol.py).

const HEADER_SIZE: usize = 9;
const MSG_RUN: u8 = 1;
const MSG_KILL: u8 = 2;
const MSG_STDOUT: u8 = 3;
const MSG_STDERR: u8 = 4;
const MSG_DONE: u8 = 5;

fn encode_frame(msg_type: u8, req_id: u32, payload: &[u8]) -> Vec<u8> {
    let plen = payload.len() as u32;
    let mut frame = Vec::with_capacity(HEADER_SIZE + payload.len());
    frame.extend_from_slice(&plen.to_be_bytes());
    frame.push(msg_type);
    frame.extend_from_slice(&req_id.to_be_bytes());
    frame.extend_from_slice(payload);
    frame
}

struct Pending {
    out: Vec<u8>,
    err: Vec<u8>,
    tx: Option<oneshot::Sender<(String, String, i32)>>,
}

/// Sandbox limits passed through to the zygote child on each execution.
pub struct SandboxLimits {
    pub uid: u32,
    pub gid: u32,
    pub net: bool,
    pub max_as: u64,
    pub timeout: Duration,
    /// When false, the zygote child skips chroot + drop_privileges
    /// and applies Landlock + O_CREAT filter instead.
    pub privilege: bool,
    /// Paths allowed by Landlock in non-privileged mode.
    pub allowed_paths: Vec<String>,
}

pub struct ZygoteManager {
    /// Only the write half is shared/locked. The read half is owned
    /// exclusively by the background reader task, so reads never hold a
    /// lock that writers need (avoids the read-across-await deadlock).
    write_half: Arc<Mutex<OwnedWriteHalf>>,
    _child: std::process::Child,
    _read_task: JoinHandle<()>,
    running: Arc<AtomicBool>,
    next_id: Mutex<u32>,
    pending: Arc<Mutex<HashMap<u32, Pending>>>,
}

impl ZygoteManager {
    pub fn new(
        python_path: &str,
        lib_so: &str,
        lib_dir: &str,
        warm_modules: &[String],
        proxy: &crate::config::ProxyConfig,
    ) -> io::Result<Self> {
        let (server_fd, worker_fd) = socketpair()?;

        let warm_str = warm_modules.join(",");
        let mut cmd = std::process::Command::new(python_path);
        cmd.env_clear();
        if let Ok(path) = std::env::var("PATH") {
            cmd.env("PATH", &path);
        }
        // Inject proxy env vars so the zygote and its forked children
        // inherit them.
        for (k, v) in proxy.proxy_env_vars() {
            cmd.env(k, v);
        }
        let child = cmd
            .args([
                "-B", "pool/zygote_worker.py",
                &worker_fd.to_string(), lib_so, lib_dir, &warm_str,
            ])
            .current_dir(LIB_PATH)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::inherit())
            .stderr(std::process::Stdio::inherit())
            .spawn()?;

        unsafe { libc::close(worker_fd); }

        let s_server = unsafe { StdUnixStream::from_raw_fd(server_fd) };
        s_server.set_nonblocking(true)?;
        let stream = UnixStream::from_std(s_server)?;
        // Split into independent read/write halves. The reader task owns the
        // read half outright; writers share only the write half. This is what
        // prevents a read().await from blocking concurrent writes.
        let (read_half, write_half) = stream.into_split();
        let write_half = Arc::new(Mutex::new(write_half));

        let running = Arc::new(AtomicBool::new(true));
        let pending = Arc::new(Mutex::new(HashMap::new()));

        let p = pending.clone();
        let r = running.clone();
        let w = write_half.clone();
        let max_output = max_output_bytes();
        let read_task =
            tokio::spawn(async move { read_loop(read_half, w, p, r, max_output).await });

        Ok(Self {
            write_half,
            _child: child,
            _read_task: read_task,
            running,
            next_id: Mutex::new(1),
            pending,
        })
    }

    pub fn is_running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    /// Execute code in the pre-warmed zygote. Returns `(stdout, stderr, exit_code)`.
    pub async fn run(
        &self,
        code_b64: &str,
        key_b64: &str,
        limits: &SandboxLimits,
    ) -> (String, String, i32) {
        if !self.running.load(Ordering::SeqCst) {
            return ("".into(), "zygote worker not running".into(), -1);
        }

        let (tx, rx) = oneshot::channel();

        let mut next = self.next_id.lock().await;
        let req_id = *next;
        *next = (*next % 0xFFFF_FFFF) + 1;
        drop(next);

        self.pending.lock().await.insert(
            req_id,
            Pending { out: Vec::new(), err: Vec::new(), tx: Some(tx) },
        );

        let payload = serde_json::json!({
            "code": code_b64, "key": key_b64,
            "uid": limits.uid, "gid": limits.gid,
            "net": limits.net, "max_as": limits.max_as,
            "privilege": limits.privilege,
            "allowed_paths": limits.allowed_paths,
        }).to_string();
        let frame = encode_frame(MSG_RUN, req_id, payload.as_bytes());

        {
            let mut w = self.write_half.lock().await;
            if w.write_all(&frame).await.is_err() {
                self.pending.lock().await.remove(&req_id);
                return ("".into(), "zygote write failed".into(), -1);
            }
        }

        match tokio::time::timeout(limits.timeout, rx).await {
            Ok(Ok(result)) => {
                self.pending.lock().await.remove(&req_id);
                result
            }
            _ => {
                let kill_frame = encode_frame(MSG_KILL, req_id, &[]);
                let mut w = self.write_half.lock().await;
                let _ = w.write_all(&kill_frame).await;
                self.pending.lock().await.remove(&req_id);
                ("".into(), "Execution timeout".into(), -1)
            }
        }
    }

    /// Gracefully stop the zygote worker, failing all pending requests.
    #[allow(dead_code)] // lifecycle hook; not yet wired to a shutdown signal
    pub async fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
        self._read_task.abort();
        // Child is killed on drop.
        self._fail_pending("zygote stopped").await;
    }

    async fn _fail_pending(&self, msg: &str) {
        let mut map = self.pending.lock().await;
        let msg = msg.to_string();
        for (_, entry) in map.drain() {
            if let Some(tx) = entry.tx {
                let _ = tx.send(("".into(), msg.clone(), -1));
            }
        }
    }
}

impl Drop for ZygoteManager {
    fn drop(&mut self) {
        self.running.store(false, Ordering::SeqCst);
        self._read_task.abort();
        // std::process::Child does NOT kill on drop (unlike tokio's Child with
        // kill_on_drop).  Without explicit kill+wait the zygote worker becomes
        // an orphan that turns into a zombie when it exits, permanently consuming
        // a PID slot.  Repeated restarts can exhaust the PID namespace.
        //
        // kill() + wait() is safe here: the zygote worker is single-threaded,
        // does not block signals, and exits promptly on SIGKILL.
        if let Err(e) = self._child.kill() {
            tracing::warn!(error = %e, "zygote: kill failed during drop");
        }
        if let Err(e) = self._child.wait() {
            tracing::warn!(error = %e, "zygote: wait failed during drop");
        }
    }
}

/// Per-request output cap (stdout + stderr). A child exceeding it is killed and
/// its request fails, so a runaway `print` loop cannot make the manager buffer
/// unbounded output into memory (OOM). Override via `ZYGOTE_MAX_OUTPUT_BYTES`.
const DEFAULT_MAX_OUTPUT_BYTES: usize = 10 * 1024 * 1024;

fn max_output_bytes() -> usize {
    std::env::var("ZYGOTE_MAX_OUTPUT_BYTES")
        .ok()
        .and_then(|v| v.parse::<usize>().ok())
        .filter(|&n| n > 0)
        .unwrap_or(DEFAULT_MAX_OUTPUT_BYTES)
}

async fn read_loop(
    mut rd: OwnedReadHalf,
    write_half: Arc<Mutex<OwnedWriteHalf>>,
    pending: Arc<Mutex<HashMap<u32, Pending>>>,
    running: Arc<AtomicBool>,
    max_output: usize,
) {
    let mut buf = vec![0u8; 65536];
    let mut frame_buf: Vec<u8> = Vec::new();

    'read: loop {
        let n = match rd.read(&mut buf).await {
            Ok(0) => break,
            Ok(n) => n,
            Err(_) => break,
        };

        frame_buf.extend_from_slice(&buf[..n]);

        while frame_buf.len() >= HEADER_SIZE {
            let plen = u32::from_be_bytes([
                frame_buf[0], frame_buf[1], frame_buf[2], frame_buf[3],
            ]) as usize;
            let mtype = frame_buf[4];
            let req_id = u32::from_be_bytes([
                frame_buf[5], frame_buf[6], frame_buf[7], frame_buf[8],
            ]);

            // Oversized frame → corrupt/hostile stream; close reader.
            if plen > max_output {
                tracing::error!(plen, max_output, "zygote: oversized frame; closing reader");
                break 'read;
            }

            if frame_buf.len() < HEADER_SIZE + plen {
                break;
            }

            let payload = frame_buf[HEADER_SIZE..HEADER_SIZE + plen].to_vec();
            frame_buf.drain(..HEADER_SIZE + plen);

            // Must release pending lock before acquiring write_half (lock ordering).
            let mut kill_req: Option<u32> = None;
            {
                let mut pending_map = pending.lock().await;
                if let Some(entry) = pending_map.get_mut(&req_id) {
                    match mtype {
                        MSG_STDOUT => entry.out.extend_from_slice(&payload),
                        MSG_STDERR => entry.err.extend_from_slice(&payload),
                        MSG_DONE => {
                            let exit_code = if payload.len() >= 4 {
                                i32::from_be_bytes([
                                    payload[0], payload[1], payload[2], payload[3],
                                ])
                            } else {
                                -1
                            };
                            let out = String::from_utf8_lossy(&entry.out).into_owned();
                            let err = String::from_utf8_lossy(&entry.err).into_owned();
                            if let Some(tx) = entry.tx.take() {
                                let _ = tx.send((out, err, exit_code));
                            }
                        }
                        _ => {}
                    }

                    if matches!(mtype, MSG_STDOUT | MSG_STDERR)
                        && entry.out.len() + entry.err.len() > max_output
                    {
                        let out = String::from_utf8_lossy(&entry.out).into_owned();
                        if let Some(tx) = entry.tx.take() {
                            let _ = tx.send((
                                out,
                                format!(
                                    "output limit exceeded (> {max_output} bytes); process killed"
                                ),
                                -1,
                            ));
                        }
                        pending_map.remove(&req_id);
                        kill_req = Some(req_id);
                    }
                }
            }
            if let Some(rid) = kill_req {
                let frame = encode_frame(MSG_KILL, rid, &[]);
                let mut w = write_half.lock().await;
                let _ = w.write_all(&frame).await;
            }
        }
    }

    // Connection lost — mark as not running and fail all pending.
    running.store(false, Ordering::SeqCst);
    tracing::error!("Python zygote connection lost; will restart on next request");

    let mut pending_map = pending.lock().await;
    for (_, entry) in pending_map.drain() {
        if let Some(tx) = entry.tx {
            let _ = tx.send(("".into(), "zygote connection lost".into(), -1));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    /// Per-request output cap: child exceeding it is killed (OOM guard).
    #[tokio::test]
    async fn per_request_output_cap_kills_child() {
        let (worker, server) = UnixStream::pair().unwrap();
        let (s_read, s_write) = server.into_split();
        let write_half = Arc::new(Mutex::new(s_write));
        let pending: Arc<Mutex<HashMap<u32, Pending>>> = Arc::new(Mutex::new(HashMap::new()));
        let running = Arc::new(AtomicBool::new(true));

        let (tx, rx) = oneshot::channel();
        pending.lock().await.insert(
            7,
            Pending { out: Vec::new(), err: Vec::new(), tx: Some(tx) },
        );

        let max_output = 100usize;
        let task = {
            let p = pending.clone();
            let r = running.clone();
            let w = write_half.clone();
            tokio::spawn(async move { read_loop(s_read, w, p, r, max_output).await })
        };

        // Worker writes 120 bytes of stdout for req 7 (> 100 cap).
        let (mut rd_worker, mut wr_worker) = worker.into_split();
        let chunk = vec![b'x'; 60];
        wr_worker.write_all(&encode_frame(MSG_STDOUT, 7, &chunk)).await.unwrap();
        wr_worker.write_all(&encode_frame(MSG_STDOUT, 7, &chunk)).await.unwrap();

        // The request completes with the limit error.
        let (_out, err, code) = tokio::time::timeout(Duration::from_secs(2), rx)
            .await
            .expect("rx timed out")
            .expect("sender dropped");
        assert_eq!(code, -1);
        assert!(err.contains("output limit exceeded"), "unexpected err: {err}");

        // The manager sent a MSG_KILL for req 7 back to the worker.
        let mut hdr = [0u8; HEADER_SIZE];
        tokio::time::timeout(Duration::from_secs(2), rd_worker.read_exact(&mut hdr))
            .await
            .expect("no kill frame")
            .expect("read kill frame");
        assert_eq!(hdr[4], MSG_KILL, "expected MSG_KILL frame");
        assert_eq!(u32::from_be_bytes([hdr[5], hdr[6], hdr[7], hdr[8]]), 7);

        // Over-limit request's buffer was freed.
        assert!(pending.lock().await.get(&7).is_none());

        task.abort();
    }

    /// Env override parsing: invalid/zero falls back to the default.
    #[test]
    fn max_output_env_parsing() {
        assert_eq!(DEFAULT_MAX_OUTPUT_BYTES, 10 * 1024 * 1024);
        // Default when unset (no other test sets this var).
        std::env::remove_var("ZYGOTE_MAX_OUTPUT_BYTES");
        assert_eq!(max_output_bytes(), DEFAULT_MAX_OUTPUT_BYTES);
    }

    /// Regression: reader parked in read().await must not block concurrent writes (C1 deadlock).
    #[tokio::test]
    async fn split_stream_read_does_not_block_write() {
        let (a, b) = UnixStream::pair().unwrap();
        let (mut a_rd, mut a_wr) = a.into_split();
        let (mut b_rd, mut b_wr) = b.into_split();

        // Reader parks in read().await — no data available yet.
        let reader = tokio::spawn(async move {
            let mut buf = [0u8; 16];
            let n = a_rd.read(&mut buf).await.unwrap();
            buf[..n].to_vec()
        });

        // Let the reader actually enter read().await before we write.
        tokio::time::sleep(Duration::from_millis(50)).await;

        // While reader is parked, write half must remain usable.
        a_wr.write_all(b"ping").await.unwrap();
        let mut pbuf = [0u8; 4];
        b_rd.read_exact(&mut pbuf).await.unwrap();
        assert_eq!(&pbuf, b"ping");

        // Peer replies; the parked reader must wake and receive it promptly.
        b_wr.write_all(b"pong").await.unwrap();
        let got = tokio::time::timeout(Duration::from_secs(2), reader)
            .await
            .expect("reader hung — C1 deadlock regression")
            .unwrap();
        assert_eq!(&got, b"pong");
    }
}
