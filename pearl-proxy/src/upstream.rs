use dashmap::DashMap;
use parking_lot::RwLock;
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

        info!(
            "[proxy] Opening dedicated upstream connection for worker '{}' -> {}:{}",
            worker_id, self.pool_host, self.pool_port
        );

        let stream = match TcpStream::connect((self.pool_host.as_str(), self.pool_port)).await {
            Ok(s) => {
                let _ = s.set_nodelay(true);
                s
            }
            Err(e) => {
                error!(
                    "[proxy] Failed to connect upstream for worker '{}': {}",
                    worker_id, e
                );
                return None;
            }
        };

        let (sse_broadcast_tx, _) = broadcast::channel(1024);
        let (submit_tx, submit_rx) = mpsc::channel::<SubmitRequest>(1024);
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

        // Spawn dedicated background task for this worker's upstream TCP session
        tokio::spawn(run_worker_upstream_loop(
            self.pool_host.clone(),
            self.pool_port,
            self.default_wallet.clone(),
            self.agent.clone(),
            self.custom_diff.clone(),
            session.clone(),
            submit_rx,
            stream,
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
            if let Some((_, session)) = self.sessions.remove(&wid) {
                info!("[reaper] Pruning idle upstream connection for worker: {}", wid);
                session.is_closing.store(true, Ordering::Relaxed);
            }
        }
    }
}

pub fn format_openai_chunk(job: &MiningNotify) -> String {
    let content = format!(
        "JOB:{}:{}:{}:{}:{}:{}",
        job.job_id,
        job.header,
        job.target,
        job.diff,
        job.cert_version,
        job.height.unwrap_or(0)
    );

    serde_json::json!({
        "id": format!("chatcmpl-{}", job.job_id),
        "object": "chat.completion.chunk",
        "created": chrono::Utc::now().timestamp(),
        "model": "gpt-4o-mini",
        "choices": [{
            "index": 0,
            "delta": {"content": content},
            "finish_reason": null
        }]
    })
    .to_string()
}

pub fn format_ping_chunk() -> String {
    serde_json::json!({
        "id": format!("chatcmpl-ping-{}", chrono::Utc::now().timestamp_millis()),
        "object": "chat.completion.chunk",
        "created": chrono::Utc::now().timestamp(),
        "model": "gpt-4o-mini",
        "choices": [{
            "index": 0,
            "delta": {"content": "PING"},
            "finish_reason": null
        }]
    })
    .to_string()
}

async fn run_worker_upstream_loop(
    _pool_host: String,
    _pool_port: u16,
    default_wallet: String,
    agent: String,
    custom_diff: String,
    session: Arc<WorkerUpstreamSession>,
    mut submit_rx: mpsc::Receiver<SubmitRequest>,
    stream: TcpStream,
    state: AppState,
) {
    let (reader, mut writer) = stream.into_split();
    let mut buf_reader = BufReader::with_capacity(65536, reader);

    let auth_pass = if !custom_diff.trim().is_empty() {
        if custom_diff.starts_with("d=") {
            custom_diff.clone()
        } else {
            format!("d={}", custom_diff.trim())
        }
    } else {
        "x".to_string()
    };

    // 1. Send mining.authorize with Kryptex Gzip v2 protocol flag
    let auth_wallet = format!("{}.{}", default_wallet, session.worker_id);
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
        error!(
            "[upstream:{}] Failed to send authorize: {}",
            session.worker_id, e
        );
        session.is_closing.store(true, Ordering::Relaxed);
        return;
    }
    let _ = writer.flush().await;

    info!(
        "[upstream:{}] Sent mining.authorize (v2 gzip) -> {} (pass: {})",
        session.worker_id, auth_wallet, auth_pass
    );

    let mut pending_submits: HashMap<u64, (oneshot::Sender<bool>, f64, String)> = HashMap::new();
    let mut line_buf = String::with_capacity(65536);

    loop {
        line_buf.clear();
        tokio::select! {
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
                                        info!(
                                            "[upstream:{}] New job: {} height={:?} diff={}",
                                            session.worker_id, job.job_id, job.height, job.diff
                                        );
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
                                        state.record_share(&session.worker_id, is_ok, hs, &job_id).await;
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

                        let submit_msg = serde_json::json!({
                            "jsonrpc": "2.0",
                            "id": mid,
                            "method": "mining.submit",
                            "params": {
                                "job_id": req.job_id,
                                "plain_proof": final_proof,
                                "hs": req.hs
                            }
                        });

                        let mut submit_str = submit_msg.to_string();
                        submit_str.push('\n');

                        if let Err(e) = writer.write_all(submit_str.as_bytes()).await {
                            error!("[upstream:{}] Failed to write submit: {}", session.worker_id, e);
                            let _ = req.response_tx.send(false);
                            break;
                        }
                        let _ = writer.flush().await;

                        pending_submits.insert(mid, (req.response_tx, req.hs, req.job_id));
                    }
                    None => {
                        // Submit channel closed
                        break;
                    }
                }
            }
        }
    }

    // Cleanup when loop exits
    info!(
        "[upstream:{}] Cleaning up dedicated connection",
        session.worker_id
    );
    session.is_closing.store(true, Ordering::Relaxed);

    // Resolve any hanging pending submissions immediately so HTTP clients do not wait for timeout
    for (_, (tx, _, _)) in pending_submits.drain() {
        let _ = tx.send(false);
    }

    let _ = writer.shutdown().await;
}
