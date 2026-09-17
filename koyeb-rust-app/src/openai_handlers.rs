use std::convert::Infallible;
use std::sync::atomic::Ordering;
use std::sync::Arc;
use std::time::Duration;
use axum::extract::{ConnectInfo, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::sse::{Event, Sse};
use axum::response::{Html, IntoResponse, Json, Response};
use tracing::{info, warn};

use crate::state::AppState;
use crate::types::{
    ChatCompletionRequest, EmbeddingRequest, FastEmbeddingRequest, ModelItem, ModelListResponse,
};
use crate::upstream::{format_openai_chunk, SubmitRequest};

struct SseStreamGuard {
    active_sse_count: Arc<std::sync::atomic::AtomicUsize>,
    last_active: Arc<std::sync::atomic::AtomicI64>,
}

impl Drop for SseStreamGuard {
    fn drop(&mut self) {
        self.active_sse_count.fetch_sub(1, Ordering::Relaxed);
        self.last_active.store(chrono::Utc::now().timestamp(), Ordering::Relaxed);
    }
}

const STATIC_PING_CHUNK: &str = "{\"id\":\"chatcmpl-ping\",\"object\":\"chat.completion.chunk\",\"created\":0,\"model\":\"gpt-4o-mini\",\"choices\":[{\"index\":0,\"delta\":{\"content\":\"PING\"},\"finish_reason\":null}]}";

fn extract_client_ip(headers: &HeaderMap, peer_addr: &std::net::SocketAddr) -> String {
    if let Some(forwarded) = headers.get("X-Forwarded-For").and_then(|v| v.to_str().ok()) {
        if let Some(first_ip) = forwarded.split(',').next() {
            let trimmed = first_ip.trim();
            if !trimmed.is_empty() {
                return trimmed.to_string();
            }
        }
    }
    peer_addr.ip().to_string()
}

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
) -> Result<Response, StatusCode> {
    let client_ip = extract_client_ip(&headers, &addr);
    let worker_id = extract_worker_id(&headers, &client_ip, payload.user.as_deref());

    info!(
        "[http] Worker connected to OpenAI chat stream: {} (IP: {})",
        worker_id, client_ip
    );
    state.update_worker_seen(&worker_id, &client_ip);

    let session = match state
        .upstream_manager
        .get_or_create(&worker_id, &client_ip, &state)
        .await
    {
        Some(s) => s,
        None => return Err(StatusCode::BAD_GATEWAY),
    };

    session.active_sse_count.fetch_add(1, Ordering::Relaxed);
    let _guard = SseStreamGuard {
        active_sse_count: session.active_sse_count.clone(),
        last_active: session.last_active.clone(),
    };

    let mut rx = session.sse_broadcast_tx.subscribe();
    let initial_job = {
        let guard = session.latest_job.read();
        guard.clone()
    };

    let session_clone = session.clone();
    let state_clone = state.clone();
    let worker_id_clone = worker_id.clone();
    let client_ip_clone = client_ip.clone();

    let stream = async_stream::stream! {
        let _guard = _guard; // Captured into stream state machine; runs Drop on client disconnect

        // If we already have a job cached from pool, yield it immediately as first token chunk
        if let Some(job) = initial_job {
            let chunk_json = format_openai_chunk(&job);
            yield Ok::<Event, Infallible>(Event::default().data(chunk_json));
        }

        let mut heartbeat = tokio::time::interval(Duration::from_secs(10));
        heartbeat.tick().await; // skip immediate first tick
        let mut tick_count: u32 = 0;

        loop {
            tokio::select! {
                _ = heartbeat.tick() => {
                    let now = chrono::Utc::now().timestamp();
                    session_clone.last_active.store(now, Ordering::Relaxed);
                    tick_count = tick_count.wrapping_add(1);
                    // Update worker stats every 3rd tick (30s) to cut DashMap lock contention by 66%
                    if tick_count % 3 == 0 {
                        state_clone.update_worker_seen(&worker_id_clone, &client_ip_clone);
                    }
                    yield Ok::<Event, Infallible>(Event::default().data(STATIC_PING_CHUNK));
                }
                res = rx.recv() => {
                    match res {
                        Ok(chunk_str) => {
                            let now = chrono::Utc::now().timestamp();
                            session_clone.last_active.store(now, Ordering::Relaxed);
                            yield Ok::<Event, Infallible>(Event::default().data(chunk_str));
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

    let sse_resp = Sse::new(stream).into_response();
    Ok(sse_resp)
}

fn parse_fallback_embeddings(
    body: &[u8],
) -> Result<(String, String, f64, Option<String>), (StatusCode, Json<serde_json::Value>)> {
    let payload: EmbeddingRequest = serde_json::from_slice(body).map_err(|_| {
        (
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": {"message": "Invalid JSON body", "type": "invalid_request_error"}})),
        )
    })?;

    let (job_id, plain_proof, hs) = match &payload.input {
        serde_json::Value::String(s) => {
            if let Some(rest) = s.strip_prefix("SUBMIT:") {
                let mut parts = rest.split(':');
                let jid = parts.next().unwrap_or("");
                let proof = parts.next().unwrap_or("");
                let hs = parts.next().and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
                (jid.to_string(), proof.to_string(), hs)
            } else if let Ok(v) = serde_json::from_str::<serde_json::Value>(s) {
                (
                    v.get("job_id").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    v.get("plain_proof").and_then(|x| x.as_str()).unwrap_or("").to_string(),
                    v.get("hs").and_then(|x| x.as_f64()).unwrap_or(0.0),
                )
            } else {
                return Err((
                    StatusCode::BAD_REQUEST,
                    Json(serde_json::json!({"error": {"message": "Unrecognized string input", "type": "invalid_request_error"}})),
                ));
            }
        }
        serde_json::Value::Object(obj) => (
            obj.get("job_id").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            obj.get("plain_proof").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            obj.get("hs").and_then(|v| v.as_f64()).unwrap_or(0.0),
        ),
        _ => {
            return Err((
                StatusCode::BAD_REQUEST,
                Json(serde_json::json!({"error": {"message": "Invalid input type", "type": "invalid_request_error"}})),
            ));
        }
    };

    Ok((job_id, plain_proof, hs, payload.user))
}

pub async fn handle_embeddings(
    State(state): State<AppState>,
    headers: HeaderMap,
    ConnectInfo(addr): ConnectInfo<std::net::SocketAddr>,
    body: axum::body::Bytes,
) -> Result<Response, (StatusCode, Json<serde_json::Value>)> {
    let client_ip = extract_client_ip(&headers, &addr);

    // Fast-path: Zero-copy parse borrowed directly from request body buffer
    let (job_id, plain_proof, hs, parsed_user) = if let Ok(fast) = serde_json::from_slice::<FastEmbeddingRequest>(&body) {
        if let Some(rest) = fast.input.strip_prefix("SUBMIT:") {
            let mut parts = rest.split(':');
            let jid = parts.next().unwrap_or("");
            let proof = parts.next().unwrap_or("");
            let hs = parts.next().and_then(|s| s.parse::<f64>().ok()).unwrap_or(0.0);
            (jid.to_string(), proof.to_string(), hs, fast.user.map(|s| s.to_string()))
        } else {
            parse_fallback_embeddings(&body)?
        }
    } else {
        parse_fallback_embeddings(&body)?
    };

    if job_id.is_empty() || plain_proof.is_empty() {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(serde_json::json!({"error": {"message": "Missing job_id or plain_proof", "type": "invalid_request_error"}})),
        ));
    }

    let worker_id = extract_worker_id(&headers, &client_ip, parsed_user.as_deref());

    let session = match state
        .upstream_manager
        .get_or_create(&worker_id, &client_ip, &state)
        .await
    {
        Some(s) => s,
        None => {
            return Err((
                StatusCode::BAD_GATEWAY,
                Json(serde_json::json!({"error": {"message": "Unable to connect upstream pool", "type": "upstream_error"}})),
            ));
        }
    };

    let now = chrono::Utc::now().timestamp();
    session.last_active.store(now, Ordering::Relaxed);

    info!(
        "[http] Received share from worker {} (IP: {}, job={}, proof_len={}, hs={:.2} TH/s)",
        worker_id, client_ip, job_id, plain_proof.len(), hs / 1e12
    );

    let (resp_tx, resp_rx) = tokio::sync::oneshot::channel();
    let req = SubmitRequest {
        job_id,
        plain_proof,
        hs,
        response_tx: resp_tx,
    };

    let send_res = tokio::time::timeout(Duration::from_secs(5), session.submit_tx.send(req)).await;
    match send_res {
        Ok(Ok(())) => {}
        Ok(Err(e)) => {
            return Err((
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": {"message": format!("Forward error: {}", e), "type": "internal_error"}})),
            ));
        }
        Err(_) => {
            return Err((
                StatusCode::GATEWAY_TIMEOUT,
                Json(serde_json::json!({"error": {"message": "Submit queue saturated or timed out", "type": "timeout_error"}})),
            ));
        }
    }

    // Await pool response with 25s timeout
    let pool_result = match tokio::time::timeout(Duration::from_secs(25), resp_rx).await {
        Ok(Ok(res)) => res,
        Ok(Err(_)) => false,
        Err(_) => false,
    };

    if pool_result {
        info!("[http] Share from worker {} ACCEPTED by pool!", worker_id);
        const ACCEPTED_RESPONSE: &[u8] = b"{\"object\":\"list\",\"data\":[{\"object\":\"embedding\",\"index\":0,\"embedding\":[0.0123,-0.0234,0.0056,-0.0189,0.0341,-0.0078,0.0192,-0.0312,0.0045,-0.0211,0.0167,-0.0093,0.0278,-0.0145,0.0089,-0.0256]}],\"model\":\"text-embedding-3-large\",\"usage\":{\"prompt_tokens\":1024,\"total_tokens\":1024},\"status\":\"accepted\"}";

        let resp = Response::builder()
            .status(StatusCode::OK)
            .header(axum::http::header::CONTENT_TYPE, "application/json")
            .body(axum::body::Body::from(ACCEPTED_RESPONSE))
            .map_err(|e| (
                StatusCode::INTERNAL_SERVER_ERROR,
                Json(serde_json::json!({"error": {"message": e.to_string(), "type": "internal_error"}})),
            ))?;
        Ok(resp)
    } else {
        warn!("[http] Share from worker {} REJECTED by pool", worker_id);
        Err((
            StatusCode::UNPROCESSABLE_ENTITY,
            Json(serde_json::json!({"error": {"message": "Share rejected by pool", "type": "invalid_request_error"}})),
        ))
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
                created: now - 86400,
                owned_by: "system".to_string(),
            },
            ModelItem {
                id: "gpt-4o-mini".to_string(),
                object: "model".to_string(),
                created: now - 86400,
                owned_by: "system".to_string(),
            },
            ModelItem {
                id: "text-embedding-3-large".to_string(),
                object: "model".to_string(),
                created: now - 86400,
                owned_by: "system".to_string(),
            },
        ],
    })
}

pub async fn handle_health() -> Json<serde_json::Value> {
    Json(serde_json::json!({
        "status": "healthy",
        "service": "openai-transparent-proxy",
        "mode": "1-to-1",
        "engine": "rust-axum-tokio-high-throughput"
    }))
}

fn is_authorized(headers: &HeaderMap, admin_pass: &str) -> bool {
    if admin_pass.trim().is_empty() {
        return false;
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
) -> Result<Response, (StatusCode, Json<serde_json::Value>)> {
    if !is_authorized(&headers, &state.admin_pass) {
        return Err((
            StatusCode::UNAUTHORIZED,
            Json(serde_json::json!({"error": "Invalid admin password"})),
        ));
    }

    let json_bytes = state.get_cached_stats_json();
    let resp = Response::builder()
        .status(StatusCode::OK)
        .header(axum::http::header::CONTENT_TYPE, "application/json")
        .body(axum::body::Body::from(json_bytes))
        .map_err(|e| (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(serde_json::json!({"error": {"message": e.to_string(), "type": "internal_error"}})),
        ))?;
    Ok(resp)
}

pub async fn handle_dashboard_html() -> Html<&'static str> {
    Html(r#"<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pearl AI Stealth Proxy (High-Performance Rust Edition)</title>
    <style>
        :root {
            --bg: #0b0f19;
            --card-bg: rgba(22, 30, 49, 0.85);
            --card-border: rgba(56, 189, 248, 0.15);
            --primary: #38bdf8;
            --primary-glow: rgba(56, 189, 248, 0.35);
            --accent: #10b981;
            --danger: #ef4444;
            --text: #f1f5f9;
            --text-dim: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            background-color: var(--bg);
            color: var(--text);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            padding: 24px;
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            margin-bottom: 24px;
            padding-bottom: 16px;
            border-bottom: 1px solid var(--card-border);
        }
        .logo { font-size: 20px; font-weight: 700; color: var(--primary); display: flex; align-items: center; gap: 8px; }
        .engine-badge {
            background: rgba(16, 185, 129, 0.15);
            color: var(--accent);
            border: 1px solid var(--accent);
            padding: 2px 8px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 600;
        }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .card {
            background: var(--card-bg);
            border: 1px solid var(--card-border);
            border-radius: 12px;
            padding: 20px;
            backdrop-filter: blur(8px);
        }
        .card-label { font-size: 12px; color: var(--text-dim); text-transform: uppercase; margin-bottom: 8px; font-weight: 600; }
        .card-val { font-size: 26px; font-weight: 700; color: var(--text); }
        .card-val.primary { color: var(--primary); }
        .card-val.accent { color: var(--accent); }
        .card-val.danger { color: var(--danger); }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; font-size: 14px; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.05); }
        th { color: var(--text-dim); font-size: 12px; text-transform: uppercase; }
        .badge {
            display: inline-block;
            padding: 3px 8px;
            border-radius: 9999px;
            font-size: 11px;
            font-weight: 600;
        }
        .badge-online { background: rgba(16, 185, 129, 0.2); color: var(--accent); }
        .badge-offline { background: rgba(239, 68, 68, 0.2); color: var(--danger); }
        .badge-share-ok { background: rgba(16, 185, 129, 0.2); color: var(--accent); }
        .badge-share-fail { background: rgba(239, 68, 68, 0.2); color: var(--danger); }
        .pool-badge { background: rgba(56, 189, 248, 0.15); color: var(--primary); padding: 2px 6px; border-radius: 4px; font-family: monospace; font-size: 12px; }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="logo">
                ⚡ Pearl ZK-PoW Stealth Proxy
                <span class="engine-badge">Rust Tokio/Axum (1000+ Workers Zero-Lag)</span>
            </div>
            <div id="pool-info" style="font-size: 13px; color: var(--text-dim);">Authentication Required</div>
        </header>

        <div id="authSection" class="card" style="margin-bottom: 24px;">
            <div class="card-label">Admin Authentication (Header-Only, Zero-Persistence)</div>
            <form id="authForm" onsubmit="handleLogin(event)" style="display:flex; gap:12px; align-items:center; margin-top:8px;">
                <input type="password" id="adminPassInput" placeholder="Enter Admin Secret / Password" required
                    style="background:rgba(15,23,42,0.8); border:1px solid var(--card-border); color:var(--text); padding:8px 12px; border-radius:6px; flex:1; outline:none; font-size:14px;">
                <button type="submit"
                    style="background:var(--primary); color:#0b0f19; font-weight:700; border:none; padding:8px 16px; border-radius:6px; cursor:pointer; font-size:14px;">
                    Unlock Dashboard
                </button>
            </form>
            <div id="authError" style="color:var(--danger); font-size:13px; margin-top:8px; display:none;"></div>
        </div>

        <div id="authActiveBar" style="display:none; justify-content:space-between; align-items:center; margin-bottom:16px; font-size:12px; color:var(--text-dim);">
            <span>🔒 Session Active in Memory (Authorization: Bearer & X-Admin-Pass)</span>
            <button onclick="handleLogout()" style="background:transparent; color:var(--danger); border:1px solid var(--danger); padding:4px 8px; border-radius:4px; cursor:pointer; font-size:11px;">Lock Dashboard</button>
        </div>

        <div class="grid">
            <div class="card">
                <div class="card-label">Active Workers</div>
                <div class="card-val primary" id="val-workers">0</div>
            </div>
            <div class="card">
                <div class="card-label">Total Hashrate</div>
                <div class="card-val accent" id="val-hashrate">0.00 TH/s</div>
            </div>
            <div class="card">
                <div class="card-label">Shares (Acc / Rej)</div>
                <div class="card-val" id="val-shares">0 / 0</div>
            </div>
            <div class="card">
                <div class="card-label">Uptime</div>
                <div class="card-val" id="val-uptime">0m</div>
            </div>
        </div>

        <div class="card" style="margin-bottom: 24px;">
            <div class="card-label">Active Mining Workers (1-to-1 Upstream Sockets)</div>
            <table>
                <thead>
                    <tr>
                        <th>Worker ID</th>
                        <th>IP Address</th>
                        <th>Status</th>
                        <th>Reported Hashrate</th>
                        <th>Shares (OK / Fail)</th>
                        <th>Last Seen</th>
                    </tr>
                </thead>
                <tbody id="workerTable">
                    <tr><td colspan="6" style="text-align:center; color:var(--text-dim);">Authenticate to view workers</td></tr>
                </tbody>
            </table>
        </div>

        <div class="card">
            <div class="card-label">Recent Share Submissions (Zlib v2 Gzip Verified)</div>
            <table>
                <thead>
                    <tr>
                        <th>Time</th>
                        <th>Worker ID</th>
                        <th>Job ID</th>
                        <th>Result</th>
                        <th>Hashrate</th>
                    </tr>
                </thead>
                <tbody id="shareTable">
                    <tr><td colspan="5" style="text-align:center; color:var(--text-dim);">Authenticate to view share history</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let adminSecret = null;
        let pollTimer = null;

        function escapeHtml(str) {
            if (str === null || str === undefined) return '';
            return String(str)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;')
                .replace(/'/g, '&#039;');
        }

        function handleLogin(e) {
            if (e) e.preventDefault();
            const input = document.getElementById('adminPassInput');
            const val = input.value.trim();
            if (!val) return;
            adminSecret = val;
            input.value = '';
            document.getElementById('authError').style.display = 'none';
            fetchStats();
            if (!pollTimer) {
                pollTimer = setInterval(fetchStats, 2000);
            }
        }

        function handleLogout() {
            adminSecret = null;
            if (pollTimer) {
                clearInterval(pollTimer);
                pollTimer = null;
            }
            document.getElementById('authSection').style.display = 'block';
            document.getElementById('authActiveBar').style.display = 'none';
            document.getElementById('pool-info').innerText = 'Authentication Required';
            document.getElementById('authError').innerText = 'Session locked.';
            document.getElementById('authError').style.display = 'block';
            document.getElementById('workerTable').innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-dim);">Authenticate to view workers</td></tr>';
            document.getElementById('shareTable').innerHTML = '<tr><td colspan="5" style="text-align:center; color:var(--text-dim);">Authenticate to view share history</td></tr>';
        }

        async function fetchStats() {
            if (!adminSecret) return;
            try {
                const res = await fetch('/admin/stats', {
                    headers: {
                        'Authorization': 'Bearer ' + adminSecret,
                        'X-Admin-Pass': adminSecret
                    }
                });

                if (res.status === 401) {
                    adminSecret = null;
                    if (pollTimer) {
                        clearInterval(pollTimer);
                        pollTimer = null;
                    }
                    document.getElementById('authSection').style.display = 'block';
                    document.getElementById('authActiveBar').style.display = 'none';
                    document.getElementById('pool-info').innerText = 'Authentication Required';
                    const errEl = document.getElementById('authError');
                    errEl.innerText = 'Unauthorized: Invalid password or rejected header.';
                    errEl.style.display = 'block';
                    return;
                }

                if (!res.ok) return;

                document.getElementById('authSection').style.display = 'none';
                document.getElementById('authActiveBar').style.display = 'flex';

                const data = await res.json();
                document.getElementById('pool-info').innerText = `Pool: ${escapeHtml(data.pool_host)} | Mode: Transparent 1-to-1`;
                document.getElementById('val-workers').innerText = data.active_workers;
                document.getElementById('val-hashrate').innerText = `${(data.total_hashrate / 1e12).toFixed(2)} TH/s`;
                document.getElementById('val-shares').innerHTML = `<span style="color:var(--accent)">${data.total_shares_accepted}</span> / <span style="color:var(--danger)">${data.total_shares_rejected}</span>`;

                const upMins = Math.floor(data.uptime_seconds / 60);
                const upHours = Math.floor(upMins / 60);
                document.getElementById('val-uptime').innerText = upHours > 0 ? `${upHours}h ${upMins % 60}m` : `${upMins}m`;

                const now = Math.floor(Date.now() / 1000);
                const wTable = document.getElementById('workerTable');
                if (data.workers && data.workers.length > 0) {
                    let html = '';
                    for (const w of data.workers) {
                        const isOnline = (now - w.last_seen) < 60;
                        const badge = isOnline ? '<span class="badge badge-online">ONLINE</span>' : '<span class="badge badge-offline">OFFLINE</span>';
                        html += `<tr>
                            <td><strong>${escapeHtml(w.worker_id)}</strong></td>
                            <td>${escapeHtml(w.ip)}</td>
                            <td>${badge}</td>
                            <td><strong>${(w.reported_hashrate / 1e12).toFixed(2)} TH/s</strong></td>
                            <td>${w.shares_accepted} / ${w.shares_rejected}</td>
                            <td>${Math.max(0, now - w.last_seen)}s ago</td>
                        </tr>`;
                    }
                    wTable.innerHTML = html;
                } else {
                    wTable.innerHTML = '<tr><td colspan="6" style="text-align:center; color:var(--text-dim);">No active workers connected</td></tr>';
                }

                const sTable = document.getElementById('shareTable');
                if (data.recent_shares && data.recent_shares.length > 0) {
                    let html = '';
                    for (const s of data.recent_shares) {
                        const resBadge = s.accepted ? '<span class="badge badge-share-ok">ACCEPTED</span>' : '<span class="badge badge-share-fail">REJECTED</span>';
                        const timeStr = escapeHtml(new Date(s.timestamp * 1000).toLocaleTimeString());
                        html += `<tr>
                            <td>${timeStr}</td>
                            <td><strong>${escapeHtml(s.worker_id)}</strong></td>
                            <td><span class="pool-badge">${escapeHtml(s.job_id)}</span></td>
                            <td>${resBadge}</td>
                            <td>${(s.hashrate / 1e12).toFixed(2)} TH/s</td>
                        </tr>`;
                    }
                    sTable.innerHTML = html;
                }
            } catch (e) {
                console.error(e);
            }
        }
    </script>
</body>
</html>"#)
}

#[cfg(test)]
mod tests {
    use super::*;
    use axum::http::HeaderValue;

    #[test]
    fn test_is_authorized_valid_header() {
        let mut headers = HeaderMap::new();
        headers.insert("X-Admin-Pass", HeaderValue::from_static("secret_token_123"));
        assert!(is_authorized(&headers, "secret_token_123"));
    }

    #[test]
    fn test_is_authorized_bearer_token() {
        let mut headers = HeaderMap::new();
        headers.insert("Authorization", HeaderValue::from_static("Bearer my_bearer_token"));
        assert!(is_authorized(&headers, "my_bearer_token"));
    }

    #[test]
    fn test_is_authorized_fail_closed_on_empty_or_whitespace() {
        let mut headers = HeaderMap::new();
        headers.insert("X-Admin-Pass", HeaderValue::from_static(""));
        assert!(!is_authorized(&headers, ""));
        assert!(!is_authorized(&headers, "   "));
    }

    #[test]
    fn test_is_authorized_invalid_credentials() {
        let mut headers = HeaderMap::new();
        headers.insert("X-Admin-Pass", HeaderValue::from_static("wrong_pass"));
        assert!(!is_authorized(&headers, "correct_pass"));

        let empty_headers = HeaderMap::new();
        assert!(!is_authorized(&empty_headers, "correct_pass"));
    }

    #[test]
    fn test_sse_stream_guard_drops_correctly() {
        use std::sync::atomic::AtomicUsize;
        let count = Arc::new(AtomicUsize::new(5));
        let last_active = Arc::new(std::sync::atomic::AtomicI64::new(100));

        {
            let _guard = SseStreamGuard {
                active_sse_count: count.clone(),
                last_active: last_active.clone(),
            };
            assert_eq!(count.load(Ordering::Relaxed), 5);
        } // Drop runs here

        assert_eq!(count.load(Ordering::Relaxed), 4);
        assert!(last_active.load(Ordering::Relaxed) > 100);
    }

    #[test]
    fn test_fast_embedding_request_parsing() {
        let raw_json = br#"{"input": "SUBMIT:job-999:plainproofbase64xyz:14500000000000.0", "user": "rig-gpu-01"}"#;
        let fast: FastEmbeddingRequest = serde_json::from_slice(raw_json).expect("fast deserialize");
        assert_eq!(fast.user, Some("rig-gpu-01"));
        assert!(fast.input.starts_with("SUBMIT:"));

        let rest = fast.input.strip_prefix("SUBMIT:").unwrap();
        let mut parts = rest.split(':');
        assert_eq!(parts.next(), Some("job-999"));
        assert_eq!(parts.next(), Some("plainproofbase64xyz"));
        assert_eq!(parts.next(), Some("14500000000000.0"));
    }

    #[test]
    fn test_fallback_embedding_request_parsing() {
        let raw_json = br#"{"input": {"job_id": "job-dict-1", "plain_proof": "proofdata123", "hs": 500000.0}, "user": "rig-fallback"}"#;
        let (jid, proof, hs, user) = parse_fallback_embeddings(raw_json).expect("fallback parse");
        assert_eq!(jid, "job-dict-1");
        assert_eq!(proof, "proofdata123");
        assert_eq!(hs, 500000.0);
        assert_eq!(user, Some("rig-fallback".to_string()));
    }
}
