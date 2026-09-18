use dashmap::DashMap;
use parking_lot::RwLock;
use rand::Rng;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::{broadcast, mpsc, oneshot, Mutex};
use tracing::{error, info, warn};

use crate::compressor::FastGzipCompressor;
use crate::state::AppState;
use crate::types::{MiningNotify, PoolJsonRpc};

static NEXT_SUBMIT_ID: AtomicU64 = AtomicU64::new(10);
pub const MAX_CONCURRENT_WORKERS: usize = 2048;

pub struct SubmitRequest {
    pub job_id: String,
    pub plain_proof: String,
    pub hs: f64,
    pub response_tx: oneshot::Sender<bool>,
}

pub struct WorkerUpstreamSession {
    pub worker_id: String,
    #[allow(dead_code)]
    pub client_ip: String,
    pub sse_broadcast_tx: broadcast::Sender<String>,
    pub submit_tx: mpsc::Sender<SubmitRequest>,
    pub latest_job: Arc<RwLock<Option<MiningNotify>>>,
    pub current_diff_bits: Arc<AtomicU64>,
    pub gzip_v2: Arc<AtomicBool>,
    pub active_sse_count: Arc<AtomicUsize>,
    pub last_active: Arc<AtomicI64>,
    pub is_closing: Arc<AtomicBool>,
}

impl WorkerUpstreamSession {
    pub fn get_diff(&self) -> f64 {
        f64::from_bits(self.current_diff_bits.load(Ordering::Relaxed))
    }

    pub fn set_diff(&self, diff: f64) {
        self.current_diff_bits.store(diff.to_bits(), Ordering::Relaxed);
    }
}

pub struct UpstreamManager {
    pub pool_host: String,
    pub pool_port: u16,
    pub default_wallet: String,
    pub agent: String,
    pub custom_diff: String,
    pub sessions: DashMap<String, Arc<WorkerUpstreamSession>>,
    creation_locks: DashMap<String, Arc<Mutex<()>>>,
}

pub const FAILOVER_POOLS: &[(&str, u16)] = &[
    ("prl.kryptex.network", 7048),
    ("prl-us.kryptex.network", 7048),
    ("prl-eu.kryptex.network", 7048),
];

async fn connect_to_pool(
    primary_host: &str,
    primary_port: u16,
    attempt: usize,
) -> Result<(TcpStream, String), std::io::Error> {
    let endpoints = [
        (primary_host, primary_port),
        (FAILOVER_POOLS[1].0, FAILOVER_POOLS[1].1),
        (FAILOVER_POOLS[2].0, FAILOVER_POOLS[2].1),
    ];
    let (host, port) = endpoints[attempt % endpoints.len()];

    let addrs = tokio::time::timeout(
        Duration::from_secs(4),
        tokio::net::lookup_host((host, port)),
    )
    .await
    .map_err(|_| std::io::Error::new(std::io::ErrorKind::TimedOut, "DNS lookup timed out"))??;

    let mut last_err = None;
    for target_addr in addrs {
        let socket = match if target_addr.is_ipv6() {
            tokio::net::TcpSocket::new_v6()
        } else {
            tokio::net::TcpSocket::new_v4()
        } {
            Ok(s) => s,
            Err(e) => {
                last_err = Some(e);
                continue;
            }
        };

        let _ = socket.set_nodelay(true);
        let _ = socket.set_keepalive(true);
        let _ = socket.set_recv_buffer_size(64 * 1024);
        let _ = socket.set_send_buffer_size(64 * 1024);

        match tokio::time::timeout(Duration::from_secs(4), socket.connect(target_addr)).await {
            Ok(Ok(stream)) => {
                let _ = stream.set_nodelay(true);
                return Ok((stream, format!("{}:{}", host, port)));
            }
            Ok(Err(e)) => last_err = Some(e),
            Err(_) => {
                last_err = Some(std::io::Error::new(
                    std::io::ErrorKind::TimedOut,
                    "Pool connect timed out",
                ))
            }
        }
    }

    Err(last_err.unwrap_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "No pool IP address could be connected")
    }))
}

impl UpstreamManager {
    pub fn new(
        pool_host: String,
        pool_port: u16,
        default_wallet: String,
        agent: String,
        custom_diff: String,
    ) -> Self {
        Self {
            pool_host,
            pool_port,
            default_wallet,
            agent,
            custom_diff,
            sessions: DashMap::new(),
            creation_locks: DashMap::new(),
        }
    }

    pub async fn get_or_create(
        &self,
        worker_id: &str,
        client_ip: &str,
        state: &AppState,
    ) -> Option<Arc<WorkerUpstreamSession>> {
        // Fast path: session already exists and active
        if let Some(session) = self.sessions.get(worker_id) {
            if !session.is_closing.load(Ordering::Relaxed) {
                let now = chrono::Utc::now().timestamp();
                session.last_active.store(now, Ordering::Relaxed);
                return Some(session.clone());
            }
        }

        // Enforce hard worker capacity before allocating locks
        if self.sessions.len() >= MAX_CONCURRENT_WORKERS && !self.sessions.contains_key(worker_id) {
            warn!(
                "[upstream] Max worker capacity ({}) reached, rejecting worker '{}'",
                MAX_CONCURRENT_WORKERS, worker_id
            );
            return None;
        }

        // Slow path: acquire worker-specific mutex to avoid duplicate upstream connections
        let lock = {
            self.creation_locks
                .entry(worker_id.to_string())
                .or_insert_with(|| Arc::new(Mutex::new(())))
                .clone()
        };

        let _guard = lock.lock().await;

        // Double check after lock
        if let Some(session) = self.sessions.get(worker_id) {
            if !session.is_closing.load(Ordering::Relaxed) {
                let now = chrono::Utc::now().timestamp();
                session.last_active.store(now, Ordering::Relaxed);
                return Some(session.clone());
            }
            self.sessions.remove(worker_id);
        }

        if self.sessions.len() >= MAX_CONCURRENT_WORKERS {
            warn!(
                "[upstream] Max worker capacity ({}) reached under lock, rejecting worker '{}'",
                MAX_CONCURRENT_WORKERS, worker_id
            );
            return None;
        }

        info!(
            "[proxy] Opening dedicated upstream connection for worker '{}' -> {}:{}",
            worker_id, self.pool_host, self.pool_port
        );

        let (stream, endpoint) = match connect_to_pool(&self.pool_host, self.pool_port, 0).await {
            Ok(res) => res,
            Err(e) => {
                error!(
                    "[proxy] Failed to connect upstream for worker '{}': {}",
                    worker_id, e
                );
                return None;
            }
        };

        info!(
            "[proxy] Opening dedicated upstream connection for worker '{}' -> {}",
            worker_id, endpoint
        );

        let (sse_broadcast_tx, _) = broadcast::channel(16);
        let (submit_tx, submit_rx) = mpsc::channel::<SubmitRequest>(32);
        let now = chrono::Utc::now().timestamp();

        let session = Arc::new(WorkerUpstreamSession {
            worker_id: worker_id.to_string(),
            client_ip: client_ip.to_string(),
            sse_broadcast_tx,
            submit_tx,
            latest_job: Arc::new(RwLock::new(None)),
            current_diff_bits: Arc::new(AtomicU64::new(1.0f64.to_bits())),
            gzip_v2: Arc::new(AtomicBool::new(false)),
            active_sse_count: Arc::new(AtomicUsize::new(0)),
            last_active: Arc::new(AtomicI64::new(now)),
            is_closing: Arc::new(AtomicBool::new(false)),
        });

        self.sessions.insert(worker_id.to_string(), session.clone());

        // Spawn dedicated background task for this worker's upstream TCP session with auto-reconnect
        tokio::spawn(run_worker_upstream_loop(
            self.pool_host.clone(),
            self.pool_port,
            self.default_wallet.clone(),
            self.agent.clone(),
            self.custom_diff.clone(),
            session.clone(),
            submit_rx,
            Some(stream),
            state.clone(),
        ));

        // Wait up to 3 seconds for initial mining.notify job from pool so HTTP clients receive it immediately
        for _ in 0..30 {
            if session.latest_job.read().is_some() {
                break;
            }
            tokio::time::sleep(Duration::from_millis(100)).await;
        }

        Some(session)
    }

    pub fn prune_idle_workers(&self) {
        let now = chrono::Utc::now().timestamp();
        let mut dead_workers = Vec::new();

        for entry in self.sessions.iter() {
            let session = entry.value();
            let sse_count = session.active_sse_count.load(Ordering::Relaxed);
            let last_active = session.last_active.load(Ordering::Relaxed);

            // If no SSE clients and inactive for > 120s, prune
            if sse_count == 0 && (now - last_active) > 120 {
                dead_workers.push(session.worker_id.clone());
            }
        }

        for wid in dead_workers {
            self.creation_locks.remove(&wid);
            if let Some((_, session)) = self.sessions.remove(&wid) {
                info!("[reaper] Pruning idle upstream connection for worker: {}", wid);
                session.is_closing.store(true, Ordering::Relaxed);
            }
        }
    }
}

pub fn format_openai_chunk(job: &MiningNotify) -> String {
    use std::fmt::Write;
    let mut out = String::with_capacity(320);
    let now = chrono::Utc::now().timestamp();
    let height = job.height.unwrap_or(0);
    let _ = write!(
        out,
        "{{\"id\":\"chatcmpl-{}\",\"object\":\"chat.completion.chunk\",\"created\":{},\"model\":\"gpt-4o-mini\",\"choices\":[{{\"index\":0,\"delta\":{{\"content\":\"JOB:{}:{}:{}:{}:{}:{}\"}},\"finish_reason\":null}}]}}",
        job.job_id, now, job.job_id, job.header, job.target, job.diff, job.cert_version, height
    );
    out
}

#[allow(dead_code)]
pub fn format_ping_chunk() -> String {
    use std::fmt::Write;
    let mut out = String::with_capacity(220);
    let now_ms = chrono::Utc::now().timestamp_millis();
    let now = now_ms / 1000;
    let _ = write!(
        out,
        "{{\"id\":\"chatcmpl-ping-{}\",\"object\":\"chat.completion.chunk\",\"created\":{},\"model\":\"gpt-4o-mini\",\"choices\":[{{\"index\":0,\"delta\":{{\"content\":\"PING\"}},\"finish_reason\":null}}]}}",
        now_ms, now
    );
    out
}

async fn run_worker_upstream_loop(
    pool_host: String,
    pool_port: u16,
    default_wallet: String,
    agent: String,
    custom_diff: String,
    session: Arc<WorkerUpstreamSession>,
    mut submit_rx: mpsc::Receiver<SubmitRequest>,
    mut initial_stream: Option<TcpStream>,
    state: AppState,
) {
    let auth_pass = if !custom_diff.trim().is_empty() {
        if custom_diff.starts_with("d=") {
            custom_diff.clone()
        } else {
            format!("d={}", custom_diff.trim())
        }
    } else {
        "x".to_string()
    };
    let auth_wallet = format!("{}.{}", default_wallet, session.worker_id);

    let mut attempt = 0;

    while !session.is_closing.load(Ordering::Relaxed) {
        let stream = if let Some(s) = initial_stream.take() {
            s
        } else {
            attempt += 1;
            let base_ms = 400u64;
            let max_ms = 12000u64;
            let exp_ms = (base_ms * (1u64 << attempt.min(5))).min(max_ms);
            let jitter_ms = rand::thread_rng().gen_range((exp_ms / 2)..=exp_ms);
            tokio::time::sleep(Duration::from_millis(jitter_ms)).await;

            match connect_to_pool(&pool_host, pool_port, attempt).await {
                Ok((s, endpoint)) => {
                    info!("[upstream:{}] Auto-reconnected to pool: {}", session.worker_id, endpoint);
                    s
                }
                Err(e) => {
                    warn!(
                        "[upstream:{}] Failed pool reconnect attempt {}: {}. Will retry with jitter",
                        session.worker_id, attempt, e
                    );
                    continue;
                }
            }
        };

        let (reader, mut writer) = stream.into_split();
        let mut buf_reader = BufReader::with_capacity(65536, reader);

        // 1. Send mining.authorize with Kryptex Gzip v2 protocol flag
        let auth_msg = serde_json::json!({
            "id": 1,
            "method": "mining.authorize",
            "params": {
                "wallet": auth_wallet,
                "agent": agent,
                "password": auth_pass,
                "type": "v2"
            }
        });

        let mut auth_payload = auth_msg.to_string();
        auth_payload.push('\n');

        if let Err(e) = writer.write_all(auth_payload.as_bytes()).await {
            warn!(
                "[upstream:{}] Failed to send authorize: {}",
                session.worker_id, e
            );
            continue;
        }
        let _ = writer.flush().await;

        info!(
            "[upstream:{}] Sent mining.authorize (v2 gzip) -> {} (pass: {})",
            session.worker_id, auth_wallet, auth_pass
        );

        const PING_PAYLOAD: &[u8] = b"{\"id\":0,\"method\":\"mining.ping\",\"params\":[]}\n";
        let mut pending_submits: HashMap<u64, (oneshot::Sender<bool>, f64, String)> = HashMap::new();
        let mut line_buf = String::with_capacity(65536);
        let mut write_buf = Vec::with_capacity(1024);
        let mut ping_interval = tokio::time::interval(Duration::from_secs(30));

        loop {
            line_buf.clear();
            tokio::select! {
                _ = ping_interval.tick() => {
                    if let Err(_) = writer.write_all(PING_PAYLOAD).await {
                        break;
                    }
                    let _ = writer.flush().await;
                }
                // Read lines from pool socket
                read_res = buf_reader.read_line(&mut line_buf) => {
                    match read_res {
                        Ok(0) => {
                            warn!("[upstream:{}] Pool closed TCP connection (EOF)", session.worker_id);
                            break;
                        }
                        Ok(_) => {
                            let trimmed = line_buf.trim();
                            if trimmed.is_empty() {
                                continue;
                            }

                            if let Ok(msg) = serde_json::from_str::<PoolJsonRpc>(trimmed) {
                                // Check if pool confirmed v2 Gzip protocol
                                if msg.get_u64_id() == Some(1) && msg.result.as_ref().and_then(|v| v.as_bool()).unwrap_or(false) {
                                    if msg.protocol_type.as_deref() == Some("v2") {
                                        session.gzip_v2.store(true, Ordering::Relaxed);
                                        info!("[upstream:{}] Pool confirmed Gzip v2 protocol active!", session.worker_id);
                                    } else {
                                        session.gzip_v2.store(false, Ordering::Relaxed);
                                        info!("[upstream:{}] Pool authorized in standard mode (no v2)", session.worker_id);
                                    }
                                }

                                // Handle mining.set_difficulty
                                if msg.method.as_deref() == Some("mining.set_difficulty") {
                                    if let Some(ref params) = msg.params {
                                        let mut new_diff = None;
                                        if let Some(arr) = params.as_array() {
                                            if !arr.is_empty() {
                                                new_diff = arr[0].as_f64();
                                            }
                                        } else if let Some(obj) = params.as_object() {
                                            if let Some(d) = obj.get("difficulty").and_then(|v| v.as_f64()) {
                                                new_diff = Some(d);
                                            }
                                        }

                                        if let Some(d) = new_diff {
                                            session.set_diff(d);
                                            info!("[upstream:{}] Pool set_difficulty: {}", session.worker_id, d);
                                            let updated_job = {
                                                let mut job_lock = session.latest_job.write();
                                                if let Some(ref mut job) = *job_lock {
                                                    job.diff = d;
                                                    Some(job.clone())
                                                } else {
                                                    None
                                                }
                                            };
                                            if let Some(job) = updated_job {
                                                let chunk = format_openai_chunk(&job);
                                                let _ = session.sse_broadcast_tx.send(chunk);
                                            }
                                        }
                                    }
                                }

                                // Handle mining.notify
                                else if msg.method.as_deref() == Some("mining.notify") {
                                    if let Some(ref params) = msg.params {
                                        let cur_diff = session.get_diff();
                                        if let Some(job) = MiningNotify::from_json_value(params, cur_diff) {
                                            attempt = 0; // Reset reconnect attempt counter on active pool traffic
                                            info!(
                                                "[upstream:{}] New job: {} height={:?} diff={}",
                                                session.worker_id, job.job_id, job.height, job.diff
                                            );
                                            *state.latest_job_id.write() = Some(job.job_id.clone());
                                            if let Some(h) = job.height {
                                                state.current_block_height.store(h, Ordering::Relaxed);
                                            }
                                            let chunk = format_openai_chunk(&job);
                                            {
                                                let mut job_lock = session.latest_job.write();
                                                *job_lock = Some(job);
                                            }
                                            let _ = session.sse_broadcast_tx.send(chunk);
                                        }
                                    }
                                }

                                // Handle submit response (id != 1)
                                else if let Some(mid) = msg.get_u64_id() {
                                    if mid != 1 {
                                        if let Some((resp_tx, hs, job_id)) = pending_submits.remove(&mid) {
                                            let is_ok = msg.error.is_none() && (
                                                msg.result.as_ref().map(|v| v == true || v == "true").unwrap_or(false)
                                            );
                                            info!(
                                                "[upstream:{}] Submit ack: ok={} job={}",
                                                session.worker_id, is_ok, job_id
                                            );
                                            state.record_share(&session.worker_id, is_ok, hs, &job_id);
                                            let _ = resp_tx.send(is_ok);
                                        }
                                    }
                                }
                            }
                        }
                        Err(e) => {
                            error!("[upstream:{}] Socket read error: {}", session.worker_id, e);
                            break;
                        }
                    }
                }

                // Handle outgoing submit requests from HTTP embeddings endpoint
                submit_cmd = submit_rx.recv() => {
                    match submit_cmd {
                        Some(req) => {
                            let mid = NEXT_SUBMIT_ID.fetch_add(1, Ordering::Relaxed);
                            let final_proof = if session.gzip_v2.load(Ordering::Relaxed) {
                                FastGzipCompressor::compress_b64_proof(&req.plain_proof)
                            } else {
                                req.plain_proof
                            };

                            write_buf.clear();
                            use std::io::Write;
                            let _ = write!(
                                write_buf,
                                "{{\"jsonrpc\":\"2.0\",\"id\":{},\"method\":\"mining.submit\",\"params\":{{\"job_id\":\"{}\",\"plain_proof\":\"{}\",\"hs\":{}}}}}\n",
                                mid, req.job_id, final_proof, req.hs
                            );

                            if let Err(e) = writer.write_all(&write_buf).await {
                                error!("[upstream:{}] Failed to write submit: {}", session.worker_id, e);
                                let _ = req.response_tx.send(false);
                                break;
                            }
                            let _ = writer.flush().await;

                            pending_submits.insert(mid, (req.response_tx, req.hs, req.job_id));
                        }
                        None => {
                            // Submit channel closed
                            session.is_closing.store(true, Ordering::Relaxed);
                            break;
                        }
                    }
                }
            }
        }

        // Drain any pending submits from broken connection
        for (_, (tx, _, _)) in pending_submits.drain() {
            let _ = tx.send(false);
        }
        let _ = writer.shutdown().await;

        if session.is_closing.load(Ordering::Relaxed) {
            break;
        }
    }

    // Cleanup when loop exits
    info!(
        "[upstream:{}] Cleaning up dedicated connection",
        session.worker_id
    );
    session.is_closing.store(true, Ordering::Relaxed);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_format_openai_chunk_valid_json() {
        let job = MiningNotify {
            job_id: "test-job-42".to_string(),
            header: "deadbeef01020304".to_string(),
            target: "00000000ffff0000".to_string(),
            diff: 4.5,
            cert_version: 3,
            height: Some(123456),
        };
        let chunk_json = format_openai_chunk(&job);
        let parsed: serde_json::Value = serde_json::from_str(&chunk_json).expect("valid JSON");
        assert_eq!(parsed["id"], "chatcmpl-test-job-42");
        assert_eq!(parsed["object"], "chat.completion.chunk");
        assert_eq!(parsed["model"], "gpt-4o-mini");
        let content = parsed["choices"][0]["delta"]["content"].as_str().unwrap();
        assert_eq!(
            content,
            "JOB:test-job-42:deadbeef01020304:00000000ffff0000:4.5:3:123456"
        );
    }

    #[test]
    fn test_format_ping_chunk_valid_json() {
        let ping_json = format_ping_chunk();
        let parsed: serde_json::Value = serde_json::from_str(&ping_json).expect("valid JSON");
        assert!(parsed["id"].as_str().unwrap().starts_with("chatcmpl-ping-"));
        assert_eq!(parsed["object"], "chat.completion.chunk");
        assert_eq!(parsed["choices"][0]["delta"]["content"], "PING");
    }
}
