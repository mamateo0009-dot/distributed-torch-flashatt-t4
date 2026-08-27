use std::convert::Infallible;
use std::time::Duration;
use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::sse::{Event, KeepAlive, Sse};
use axum::response::{Html, IntoResponse, Json};
use futures_util::stream::Stream;
use rand::Rng;
use tracing::{info, warn};

use crate::state::{AppState, UpstreamCommand};
use crate::types::{
    ChatChoice, ChatChoiceDelta, ChatCompletionChunk, ChatCompletionRequest, EmbeddingItem,
    EmbeddingRequest, EmbeddingResponse, EmbeddingUsage, MiningNotify, ModelItem,
    ModelListResponse, ProxyStatsResponse, WorkerStats,
};

fn extract_worker_id(headers: &HeaderMap, ip: &str, req_user: Option<&str>) -> String {
    if let Some(w) = headers.get("X-Worker-Id").and_then(|v| v.to_str().ok()) {
        if !w.trim().is_empty() {
            return w.trim().to_string();
        }
    }
    if let Some(w) = headers.get("X-Worker-Name").and_then(|v| v.to_str().ok()) {
        if !w.trim().is_empty() {
            return w.trim().to_string();
        }
    }
    if let Some(user) = req_user {
        if !user.trim().is_empty() {
            return user.trim().to_string();
        }
    }
    // Fallback: format based on client IP
    format!("vps-{}", ip.replace(['.', ':', '%'], "-"))
}

pub async fn handle_chat_completions(
    State(state): State<AppState>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    Json(payload): Json<ChatCompletionRequest>,
) -> impl IntoResponse {
    let client_ip = addr.ip().to_string();
    let worker_id = extract_worker_id(&headers, &client_ip, payload.user.as_deref());

    info!("Worker connected via OpenAI chat stream: {} (IP: {})", worker_id, client_ip);
    state.update_worker_seen(&worker_id, &client_ip).await;

    let mut rx = state.job_broadcast_tx.subscribe();
    let initial_job = {
        let guard = state.latest_job.read().await;
        guard.clone()
    };

    let worker_id_clone = worker_id.clone();
    let state_clone = state.clone();
    let client_ip_clone = client_ip.clone();

    let stream = async_stream::stream! {
        // If we already have a job cached from pool, yield it immediately as first token chunk
        if let Some(job) = initial_job {
            let chunk_json = format_job_as_openai_chunk(&job);
            yield Ok::<Event, Infallible>(Event::default().data(chunk_json));
        }

        let mut heartbeat = tokio::time::interval(Duration::from_secs(15));
        loop {
            tokio::select! {
                _ = heartbeat.tick() => {
                    state_clone.update_worker_seen(&worker_id_clone, &client_ip_clone).await;
                    let ping_chunk = create_heartbeat_chunk();
                    yield Ok::<Event, Infallible>(Event::default().data(ping_chunk));
                }
                res = rx.recv() => {
                    match res {
                        Ok(notify) => {
                            let chunk_json = format_job_as_openai_chunk(&notify);
                            yield Ok::<Event, Infallible>(Event::default().data(chunk_json));
                        }
                        Err(tokio::sync::broadcast::error::RecvError::Lagged(n)) => {
                            warn!("Worker {} lagged by {} jobs", worker_id_clone, n);
                        }
                        Err(tokio::sync::broadcast::error::RecvError::Closed) => {
                            break;
                        }
                    }
                }
            }
        }
    };

    Sse::new(stream).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)).text("keepalive"))
}

fn format_job_as_openai_chunk(notify: &MiningNotify) -> String {
    // Pack job into realistic OpenAI delta content
    let job_encoded = format!(
        "JOB:{}:{}:{}:{}:{}:{}",
        notify.job_id,
        notify.header,
        notify.target,
        notify.diff,
        notify.cert_version,
        notify.height.unwrap_or(0)
    );

    let chunk = ChatCompletionChunk {
        id: format!("chatcmpl-{}", notify.job_id),
        object: "chat.completion.chunk".to_string(),
        created: chrono::Utc::now().timestamp(),
        model: "gpt-4o-mini".to_string(),
        choices: vec![ChatChoice {
            index: 0,
            delta: ChatChoiceDelta {
                content: Some(job_encoded),
                role: None,
            },
            finish_reason: None,
        }],
    };

    serde_json::to_string(&chunk).unwrap_or_default()
}

fn create_heartbeat_chunk() -> String {
    let chunk = ChatCompletionChunk {
        id: format!("chatcmpl-ping-{}", chrono::Utc::now().timestamp_millis()),
        object: "chat.completion.chunk".to_string(),
        created: chrono::Utc::now().timestamp(),
        model: "gpt-4o-mini".to_string(),
        choices: vec![ChatChoice {
            index: 0,
            delta: ChatChoiceDelta {
                content: Some("PING".to_string()),
                role: None,
            },
            finish_reason: None,
        }],
    };

    serde_json::to_string(&chunk).unwrap_or_default()
}

pub async fn handle_embeddings(
    State(state): State<AppState>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    Json(payload): Json<EmbeddingRequest>,
) -> Result<Json<EmbeddingResponse>, (StatusCode, String)> {
    let client_ip = addr.ip().to_string();
    let worker_id = extract_worker_id(&headers, &client_ip, payload.user.as_deref());

    // Parse input from embedding request
    // Expected format: "SUBMIT:<job_id>:<plain_proof>:<hs>" or JSON string
    let input_str = match &payload.input {
        serde_json::Value::String(s) => s.clone(),
        serde_json::Value::Object(obj) => {
            let job_id = obj.get("job_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let plain_proof = obj.get("plain_proof").and_then(|v| v.as_str()).unwrap_or("").to_string();
            let hs = obj.get("hs").and_then(|v| v.as_f64()).unwrap_or(0.0);
            format!("SUBMIT:{}:{}:{}", job_id, plain_proof, hs)
        }
        _ => return Err((StatusCode::BAD_REQUEST, "Invalid input format".to_string())),
    };

    let mut job_id = String::new();
    let mut plain_proof = String::new();
    let mut hs = 0.0;

    if input_str.starts_with("SUBMIT:") {
        let parts: Vec<&str> = input_str.splitn(4, ':').collect();
        if parts.len() >= 4 {
            job_id = parts[1].to_string();
            plain_proof = parts[2].to_string();
            hs = parts[3].parse::<f64>().unwrap_or(0.0);
        }
    } else if let Ok(json_val) = serde_json::from_str::<serde_json::Value>(&input_str) {
        job_id = json_val.get("job_id").and_then(|v| v.as_str()).unwrap_or("").to_string();
        plain_proof = json_val.get("plain_proof").and_then(|v| v.as_str()).unwrap_or("").to_string();
        hs = json_val.get("hs").and_then(|v| v.as_f64()).unwrap_or(0.0);
    }

    if job_id.is_empty() || plain_proof.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            "Missing job_id or plain_proof in submission".to_string(),
        ));
    }

    info!(
        "Received disguised share from worker {} (IP: {}, job={}, proof_len={}, hs={:.2} GMAC/s)",
        worker_id, client_ip, job_id, plain_proof.len(), hs / 1e9
    );

    let (resp_tx, resp_rx) = tokio::sync::oneshot::channel();
    let cmd = UpstreamCommand::SubmitShare {
        job_id,
        plain_proof,
        hs,
        worker_id: worker_id.clone(),
        response_tx: resp_tx,
    };

    if let Err(e) = state.upstream_tx.send(cmd).await {
        return Err((
            StatusCode::INTERNAL_SERVER_ERROR,
            format!("Failed to forward share to upstream: {}", e),
        ));
    }

    // Await pool response with 12s timeout
    let pool_result = match tokio::time::timeout(Duration::from_secs(12), resp_rx).await {
        Ok(Ok(res)) => res,
        Ok(Err(_)) => Err("Response channel canceled".to_string()),
        Err(_) => Err("Upstream pool timeout".to_string()),
    };

    match pool_result {
        Ok(true) => {
            info!("Share from worker {} ACCEPTED by pool!", worker_id);
            // Return realistic OpenAI embedding vector
            let mut rng = rand::thread_rng();
            let fake_vector: Vec<f32> = (0..16).map(|_| rng.gen_range(-0.05..0.05)).collect();

            Ok(Json(EmbeddingResponse {
                object: "list".to_string(),
                data: vec![EmbeddingItem {
                    object: "embedding".to_string(),
                    index: 0,
                    embedding: fake_vector,
                }],
                model: "text-embedding-3-large".to_string(),
                usage: EmbeddingUsage {
                    prompt_tokens: 1024,
                    total_tokens: 1024,
                },
            }))
        }
        Ok(false) | Err(_) => {
            warn!("Share from worker {} REJECTED or failed: {:?}", worker_id, pool_result);
            Err((
                StatusCode::UNPROCESSABLE_ENTITY,
                "Share rejected by upstream pool".to_string(),
            ))
        }
    }
}

pub async fn handle_models_list() -> Json<ModelListResponse> {
    let now = chrono::Utc::now().timestamp();
    Json(ModelListResponse {
        object: "list".to_string(),
        data: vec![
            ModelItem {
                id: "gpt-4o".to_string(),
                object: "model".to_string(),
                created: now - 86400 * 30,
                owned_by: "system".to_string(),
            },
            ModelItem {
                id: "gpt-4o-mini".to_string(),
                object: "model".to_string(),
                created: now - 86400 * 20,
                owned_by: "system".to_string(),
            },
            ModelItem {
                id: "text-embedding-3-large".to_string(),
                object: "model".to_string(),
                created: now - 86400 * 60,
                owned_by: "system".to_string(),
            },
        ],
    })
}

#[derive(serde::Deserialize, Default)]
pub struct AuthQuery {
    pub pass: Option<String>,
}

fn is_authorized(headers: &HeaderMap, query: &AuthQuery, admin_pass: &str) -> bool {
    if admin_pass.is_empty() {
        return true;
    }
    if let Some(ref p) = query.pass {
        if p == admin_pass {
            return true;
        }
    }
    if let Some(p) = headers.get("X-Admin-Pass").and_then(|v| v.to_str().ok()) {
        if p == admin_pass {
            return true;
        }
    }
    if let Some(auth) = headers.get("Authorization").and_then(|v| v.to_str().ok()) {
        if let Some(token) = auth.strip_prefix("Bearer ") {
            if token == admin_pass {
                return true;
            }
        }
    }
    false
}

pub async fn handle_admin_stats(
    State(state): State<AppState>,
    headers: HeaderMap,
    axum::extract::Query(query): axum::extract::Query<AuthQuery>,
) -> Result<Json<ProxyStatsResponse>, (StatusCode, String)> {
    if !is_authorized(&headers, &query, &state.admin_pass) {
        return Err((StatusCode::UNAUTHORIZED, "Invalid admin password".to_string()));
    }

    let now = chrono::Utc::now().timestamp();
    let uptime = (now - state.start_time).max(0) as u64;

    let workers_guard = state.workers.read().await;
    let mut workers_list: Vec<WorkerStats> = workers_guard.values().cloned().collect();
    workers_list.sort_by(|a, b| b.last_seen.cmp(&a.last_seen));

    let active_workers = workers_list.iter().filter(|w| now - w.last_seen < 60).count();
    let total_hashrate: f64 = workers_list
        .iter()
        .filter(|w| now - w.last_seen < 60)
        .map(|w| w.reported_hashrate)
        .sum();

    let total_accepted = *state.total_accepted.read().await;
    let total_rejected = *state.total_rejected.read().await;

    let (current_job_id, current_block_height) = {
        let guard = state.latest_job.read().await;
        if let Some(ref j) = *guard {
            (Some(j.job_id.clone()), j.height)
        } else {
            (None, None)
        }
    };

    let share_logs = {
        let guard = state.share_logs.read().await;
        guard.clone()
    };

    Ok(Json(ProxyStatsResponse {
        pool_host: state.pool_host.clone(),
        default_wallet: state.default_wallet.clone(),
        uptime_seconds: uptime,
        active_workers,
        total_shares_accepted: total_accepted,
        total_shares_rejected: total_rejected,
        total_hashrate,
        current_job_id,
        current_block_height,
        workers: workers_list,
        recent_shares: share_logs,
    }))
}

pub async fn handle_dashboard_html(State(state): State<AppState>) -> Html<String> {
    let html = format!(
        r#"<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pearl AI Stealth Proxy & Miner Hub</title>
    <style>
        :root {{
            --bg: #0b0f19;
            --card-bg: rgba(22, 30, 49, 0.85);
            --card-border: rgba(56, 189, 248, 0.15);
            --primary: #38bdf8;
            --primary-glow: rgba(56, 189, 248, 0.35);
            --accent: #10b981;
            --danger: #ef4444;
            --text: #f1f5f9;
            --text-dim: #94a3b8;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            padding: 24px;
            background-image: radial-gradient(circle at 50% 0%, #1e293b 0%, #0b0f19 75%);
        }}
        .container {{ max-width: 1200px; margin: 0 auto; }}
        header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }}
        .brand {{ display: flex; align-items: center; gap: 12px; }}
        .brand-icon {{ width: 36px; height: 36px; background: linear-gradient(135deg, #38bdf8, #6366f1); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #fff; }}
        .title {{ font-size: 20px; font-weight: 700; color: #fff; }}
        .subtitle {{ font-size: 13px; color: var(--text-dim); margin-top: 2px; }}
        .auth-bar {{ display: flex; align-items: center; gap: 8px; }}
        .pass-input {{ background: #1e293b; border: 1px solid #334155; color: #fff; padding: 8px 12px; border-radius: 6px; font-size: 13px; outline: none; }}
        .btn {{ background: #0284c7; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; transition: 0.2s; }}
        .btn:hover {{ background: #38bdf8; color: #0f172a; }}
        .card {{ background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 12px; padding: 20px; margin-bottom: 20px; backdrop-filter: blur(8px); box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }}
        .grid-stats {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; margin-bottom: 24px; }}
        .stat-box {{ background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255,255,255,0.05); padding: 18px; border-radius: 10px; text-align: center; }}
        .stat-val {{ font-size: 26px; font-weight: 800; color: var(--primary); letter-spacing: -0.5px; text-shadow: 0 0 12px var(--primary-glow); }}
        .stat-lbl {{ font-size: 12px; text-transform: uppercase; color: var(--text-dim); font-weight: 600; margin-top: 6px; letter-spacing: 0.5px; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 12px; }}
        th, td {{ padding: 12px 16px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06); font-size: 14px; }}
        th {{ background: rgba(30, 41, 59, 0.9); color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }}
        tr:hover td {{ background: rgba(255,255,255,0.02); }}
        .badge {{ padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; display: inline-block; }}
        .badge-online {{ background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }}
        .badge-offline {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }}
        .badge-share-ok {{ background: rgba(16, 185, 129, 0.2); color: #34d399; }}
        .badge-share-fail {{ background: rgba(239, 68, 68, 0.2); color: #f87171; }}
        .pool-badge {{ font-family: monospace; background: #0f172a; padding: 4px 8px; border-radius: 4px; color: #38bdf8; font-size: 12px; }}
        .live-dot {{ width: 8px; height: 8px; background: #10b981; border-radius: 50%; display: inline-block; margin-right: 6px; box-shadow: 0 0 8px #10b981; animation: pulse 2s infinite; }}
        @keyframes pulse {{ 0% {{ opacity: 1; }} 50% {{ opacity: 0.3; }} 100% {{ opacity: 1; }} }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <div class="brand-icon">AI</div>
                <div>
                    <div class="title"><span class="live-dot"></span>Pearl AI Stealth Proxy & Miner Hub</div>
                    <div class="subtitle">OpenAI Camouflage Gateway | Upstream: <span id="poolHost" class="pool-badge">{}</span></div>
                </div>
            </div>
            <div class="auth-bar">
                <input type="password" id="passInput" class="pass-input" placeholder="Admin Password" />
                <button class="btn" onclick="savePass()">Unlock</button>
            </div>
        </header>

        <div class="grid-stats">
            <div class="stat-box">
                <div class="stat-val" id="totalHash">-- TH/s</div>
                <div class="stat-lbl">Total Hashrate</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" id="activeWorkers">0</div>
                <div class="stat-lbl">Active VPS Workers</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" id="acceptedShares" style="color:var(--accent);">0</div>
                <div class="stat-lbl">Accepted Shares</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" id="rejectedShares" style="color:var(--danger);">0</div>
                <div class="stat-lbl">Rejected Shares</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" id="blockHeight" style="color:#a855f7;">--</div>
                <div class="stat-lbl">Block Height</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" id="uptime">0s</div>
                <div class="stat-lbl">Proxy Uptime</div>
            </div>
        </div>

        <div class="card">
            <h2 style="font-size:16px; margin-bottom:12px; color:#fff;">Active Mining Workers</h2>
            <table>
                <thead>
                    <tr><th>Worker ID</th><th>IP Address</th><th>Status</th><th>Reported Hashrate</th><th>Shares (Acc / Rej)</th><th>Last Active</th></tr>
                </thead>
                <tbody id="workerTable">
                    <tr><td colspan="6" style="text-align:center; color:var(--text-dim);">No active workers connected</td></tr>
                </tbody>
            </table>
        </div>

        <div class="card">
            <h2 style="font-size:16px; margin-bottom:12px; color:#fff;">Live Proof & Share Event Feed</h2>
            <table>
                <thead>
                    <tr><th>Time</th><th>Worker</th><th>Job ID</th><th>Result</th><th>Hashrate</th></tr>
                </thead>
                <tbody id="shareTable">
                    <tr><td colspan="5" style="text-align:center; color:var(--text-dim);">No shares submitted yet</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let currentPass = localStorage.getItem('proxy_admin_pass') || '';
        if (currentPass) {{
            document.getElementById('passInput').value = currentPass;
        }}

        function savePass() {{
            const val = document.getElementById('passInput').value.trim();
            localStorage.setItem('proxy_admin_pass', val);
            currentPass = val;
            fetchStats();
        }}

        async function fetchStats() {{
            try {{
                const url = '/admin/stats?pass=' + encodeURIComponent(currentPass);
                const res = await fetch(url);
                if (res.status === 401) {{
                    document.getElementById('workerTable').innerHTML = '<tr><td colspan="6" style="text-align:center; color:#ef4444; font-weight:bold;">Authentication Required: Enter Admin Password Above</td></tr>';
                    return;
                }}
                const data = await res.json();

                document.getElementById('poolHost').textContent = data.pool_host;
                document.getElementById('totalHash').textContent = (data.total_hashrate / 1e12).toFixed(2) + ' TH/s';
                document.getElementById('activeWorkers').textContent = data.active_workers;
                document.getElementById('acceptedShares').textContent = data.total_shares_accepted;
                document.getElementById('rejectedShares').textContent = data.total_shares_rejected;
                document.getElementById('blockHeight').textContent = data.current_block_height || 'N/A';
                document.getElementById('uptime').textContent = data.uptime_seconds + 's';

                // Workers
                const wTable = document.getElementById('workerTable');
                if (data.workers && data.workers.length > 0) {{
                    const now = Math.floor(Date.now() / 1000);
                    let html = '';
                    for (const w of data.workers) {{
                        const isOnline = (now - w.last_seen) < 60;
                        const badge = isOnline ? '<span class="badge badge-online">ONLINE</span>' : '<span class="badge badge-offline">OFFLINE</span>';
                        html += `<tr>
                            <td><strong>${{w.worker_id}}</strong></td>
                            <td>${{w.ip}}</td>
                            <td>${{badge}}</td>
                            <td><strong>${{(w.reported_hashrate / 1e12).toFixed(2)}} TH/s</strong></td>
                            <td>${{w.shares_accepted}} / ${{w.shares_rejected}}</td>
                            <td>${{Math.max(0, now - w.last_seen)}}s ago</td>
                        </tr>`;
                    }}
                    wTable.innerHTML = html;
                }} else {{
                    wTable.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-dim);">No active workers connected</td></tr>';
                }}

                // Recent Shares
                const sTable = document.getElementById('shareTable');
                if (data.recent_shares && data.recent_shares.length > 0) {{
                    let html = '';
                    for (const s of data.recent_shares) {{
                        const resBadge = s.accepted ? '<span class="badge badge-share-ok">ACCEPTED</span>' : '<span class="badge badge-share-fail">REJECTED</span>';
                        const timeStr = new Date(s.timestamp * 1000).toLocaleTimeString();
                        html += `<tr>
                            <td>${{timeStr}}</td>
                            <td><strong>${{s.worker_id}}</strong></td>
                            <td><span class="pool-badge">${{s.job_id}}</span></td>
                            <td>${{resBadge}}</td>
                            <td>${{(s.hashrate / 1e12).toFixed(2)}} TH/s</td>
                        </tr>`;
                    }}
                    sTable.innerHTML = html;
                }}
            }} catch (e) {{
                console.error(e);
            }}
        }}

        fetchStats();
        setInterval(fetchStats, 2000);
    </script>
</body>
</html>"#,
        state.pool_host
    );

    Html(html)
}

pub async fn handle_health() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "healthy",
        "service": "openai-inference-gateway",
        "timestamp": chrono::Utc::now().timestamp()
    }))
}
