use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::{mpsc, oneshot, RwLock};
use tracing::{error, info, warn};

use crate::state::{AppState, UpstreamCommand};
use crate::types::{MiningNotify, PoolJsonRpc};

static NEXT_MSG_ID: AtomicU64 = AtomicU64::new(10);

pub async fn run_upstream_client(
    pool_host: String,
    pool_port: u16,
    wallet: String,
    worker: String,
    agent: String,
    state: AppState,
    mut cmd_rx: mpsc::Receiver<UpstreamCommand>,
) {
    let pending_submits: Arc<RwLock<HashMap<u64, (String, f64, String, oneshot::Sender<Result<bool, String>>)>>> =
        Arc::new(RwLock::new(HashMap::new()));

    loop {
        info!("Connecting to upstream pool {}:{}...", pool_host, pool_port);
        match TcpStream::connect((pool_host.as_str(), pool_port)).await {
            Ok(stream) => {
                info!("Connected to upstream pool {}:{}", pool_host, pool_port);
                let (reader, mut writer) = stream.into_split();
                let mut buf_reader = BufReader::new(reader);

                // Send mining.authorize
                let auth_msg = serde_json::json!({
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "mining.authorize",
                    "params": {
                        "wallet": wallet,
                        "worker": worker,
                        "agent": agent
                    }
                });
                let mut auth_str = auth_msg.to_string();
                auth_str.push('\n');

                if let Err(e) = writer.write_all(auth_str.as_bytes()).await {
                    error!("Failed to send authorize to pool: {}", e);
                    tokio::time::sleep(Duration::from_secs(3)).await;
                    continue;
                }
                info!("Sent mining.authorize for wallet {}", wallet);

                let pending_for_reader = pending_submits.clone();
                let state_for_reader = state.clone();

                // Spawn task to read from pool
                let (stop_tx, mut stop_rx) = tokio::sync::oneshot::channel::<()>();

                let reader_handle = tokio::spawn(async move {
                    let mut line = String::new();
                    loop {
                        line.clear();
                        match buf_reader.read_line(&mut line).await {
                            Ok(0) => {
                                warn!("Upstream pool closed TCP connection (EOF)");
                                break;
                            }
                            Ok(_) => {
                                let trimmed = line.trim();
                                if trimmed.is_empty() {
                                    continue;
                                }

                                if let Ok(rpc) = serde_json::from_str::<PoolJsonRpc>(trimmed) {
                                    // Check if it's mining.notify
                                    if let Some(ref method) = rpc.method {
                                        if method == "mining.notify" {
                                            if let Some(ref params) = rpc.params {
                                                if let Ok(notify) = serde_json::from_value::<MiningNotify>(params.clone()) {
                                                    info!(
                                                        "Pool job update: job_id={}, diff={}, height={:?}",
                                                        notify.job_id, notify.diff, notify.height
                                                    );
                                                    {
                                                        let mut latest = state_for_reader.latest_job.write().await;
                                                        *latest = Some(notify.clone());
                                                    }
                                                    // Broadcast job to all active SSE workers
                                                    let _ = state_for_reader.job_broadcast_tx.send(notify);
                                                }
                                            }
                                        }
                                    }

                                    // Check if it's a response to mining.submit
                                    if let Some(id) = rpc.id {
                                        let mut pending = pending_for_reader.write().await;
                                        if let Some((worker_id, hs, job_id, resp_tx)) = pending.remove(&id) {
                                            let is_ok = rpc.error.is_none() && rpc.result.as_ref().and_then(|v| v.as_bool()).unwrap_or(false);
                                            info!(
                                                "Pool submit response for worker {}: accepted={}, error={:?}",
                                                worker_id, is_ok, rpc.error
                                            );
                                            state_for_reader.record_share(&worker_id, is_ok, hs, &job_id).await;
                                            let _ = resp_tx.send(if is_ok {
                                                Ok(true)
                                            } else {
                                                Err(format!("{:?}", rpc.error))
                                            });
                                        }
                                    }
                                }
                            }
                            Err(e) => {
                                error!("Error reading from pool socket: {}", e);
                                break;
                            }
                        }
                    }
                    let _ = stop_tx.send(());
                });

                // Command processing loop for sending submissions
                loop {
                    tokio::select! {
                        _ = &mut stop_rx => {
                            warn!("Reader thread stopped, reconnecting upstream...");
                            break;
                        }
                        cmd = cmd_rx.recv() => {
                            match cmd {
                                Some(UpstreamCommand::SubmitShare { job_id, plain_proof, hs, worker_id, response_tx }) => {
                                    let msg_id = NEXT_MSG_ID.fetch_add(1, Ordering::Relaxed);
                                    let submit_msg = serde_json::json!({
                                        "jsonrpc": "2.0",
                                        "id": msg_id,
                                        "method": "mining.submit",
                                        "params": {
                                            "job_id": job_id.clone(),
                                            "plain_proof": plain_proof,
                                            "hs": hs
                                        }
                                    });
                                    let mut submit_str = submit_msg.to_string();
                                    submit_str.push('\n');

                                    info!("Forwarding submit for worker {} to pool (msg_id={})", worker_id, msg_id);
                                    {
                                        let mut pending = pending_submits.write().await;
                                        pending.insert(msg_id, (worker_id, hs, job_id, response_tx));
                                    }

                                    if let Err(e) = writer.write_all(submit_str.as_bytes()).await {
                                        error!("Failed to write submit to pool: {}", e);
                                        break;
                                    }
                                }
                                None => {
                                    info!("Upstream command channel closed");
                                    reader_handle.abort();
                                    return;
                                }
                            }
                        }
                    }
                }

                reader_handle.abort();
                let _ = reader_handle.await;

                // Resolve and clear any orphaned pending submissions on disconnect
                {
                    let mut pending = pending_submits.write().await;
                    for (_, (_, _, _, tx)) in pending.drain() {
                        let _ = tx.send(Ok(false));
                    }
                }
            }
            Err(e) => {
                error!("Failed to connect to pool {}:{}: {}", pool_host, pool_port, e);
            }
        }

        info!("Waiting 3s before reconnecting to pool...");
        tokio::time::sleep(Duration::from_secs(3)).await;
    }
}
