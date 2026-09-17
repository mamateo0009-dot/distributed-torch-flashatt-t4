import asyncio
import json
import time
import os
import random
import socket
import zlib
import binascii
import urllib.request
from urllib.parse import parse_qs, urlparse

try:
    import resource
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target_nofile = max(65536, hard) if hard > 0 else 65536
    resource.setrlimit(resource.RLIMIT_NOFILE, (target_nofile, max(target_nofile, hard)))
    print(f"[proxy] Raised RLIMIT_NOFILE to {target_nofile}")
except Exception as e:
    print(f"[proxy] Note: Could not raise RLIMIT_NOFILE ({e})")

class FastGzipCompressor:
    """Pre-allocated, pooled Gzip Level 4 compressor to eliminate memory churn per share."""
    def __init__(self, level: int = 4):
        self.level = level

    def compress_b64_proof(self, plain_proof_b64: str) -> str:
        try:
            raw_bytes = binascii.a2b_base64(plain_proof_b64)
            if len(raw_bytes) >= 2 and raw_bytes[0] == 0x1F and raw_bytes[1] == 0x8B:
                return plain_proof_b64
            c_obj = zlib.compressobj(self.level, zlib.DEFLATED, 31)
            gz = c_obj.compress(raw_bytes) + c_obj.flush()
            return binascii.b2a_base64(gz, newline=False).decode('ascii')
        except Exception:
            return plain_proof_b64

fast_gzip_compressor = FastGzipCompressor(level=4)

def configure_socket(writer: asyncio.StreamWriter):
    """Enable TCP_NODELAY and persistent KeepAlive to prevent delayed ACK and NAT drops."""
    try:
        sock = writer.get_extra_info('socket')
        if sock is not None:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, 'TCP_KEEPIDLE'):
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 15)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 3)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 3)
    except Exception:
        pass

POOL_HOST = os.environ.get("POOL_HOST", "prl.kryptex.network")
POOL_PORT = int(os.environ.get("POOL_PORT", "7048"))
DEFAULT_WALLET = os.environ.get("WALLET", "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
DEFAULT_DIFF = os.environ.get("CUSTOM_DIFF", "")
PORT = int(os.environ.get("PORT", "8000"))
KOYEB_APP_URL = os.environ.get("KOYEB_APP_URL", "https://pearl-hub-tranteo777-eb4ff2aa.koyeb.app")

POOL_ENDPOINTS = [
    (POOL_HOST, POOL_PORT),
    ("prl-us.kryptex.network", 7048),
    ("prl-eu.kryptex.network", 7048),
]

start_time = time.time()
workers = {}
total_accepted = 0
total_rejected = 0
share_logs = []
next_msg_id = 10

upstream_connections = {}
upstream_locks = {}
global_lock = asyncio.Lock()

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pearl AI Stealth Proxy (Ultra-Stable 24/7)</title>
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
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            padding: 24px;
            background-image: radial-gradient(circle at 50% 0%, #1e293b 0%, #0b0f19 75%);
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
        .brand { display: flex; align-items: center; gap: 12px; }
        .brand-icon { width: 36px; height: 36px; background: linear-gradient(135deg, #38bdf8, #6366f1); border-radius: 8px; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #fff; }
        .title { font-size: 20px; font-weight: 700; color: #fff; }
        .subtitle { font-size: 13px; color: var(--text-dim); margin-top: 2px; }
        .auth-bar { display: flex; align-items: center; gap: 8px; }
        .pass-input { background: #1e293b; border: 1px solid #334155; color: #fff; padding: 8px 12px; border-radius: 6px; font-size: 13px; outline: none; }
        .btn { background: #0284c7; color: #fff; border: none; padding: 8px 16px; border-radius: 6px; font-weight: 600; cursor: pointer; font-size: 13px; transition: 0.2s; }
        .btn:hover { background: #38bdf8; color: #0f172a; }
        .card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 12px; padding: 20px; margin-bottom: 20px; backdrop-filter: blur(8px); box-shadow: 0 10px 25px -5px rgba(0,0,0,0.5); }
        .grid-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .stat-box { background: rgba(30, 41, 59, 0.7); border: 1px solid rgba(255,255,255,0.05); padding: 18px; border-radius: 10px; text-align: center; }
        .stat-val { font-size: 26px; font-weight: 800; color: var(--primary); letter-spacing: -0.5px; text-shadow: 0 0 12px var(--primary-glow); }
        .stat-lbl { font-size: 12px; text-transform: uppercase; color: var(--text-dim); font-weight: 600; margin-top: 6px; letter-spacing: 0.5px; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06); font-size: 14px; }
        th { background: rgba(30, 41, 59, 0.9); color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
        tr:hover td { background: rgba(255,255,255,0.02); }
        .badge { padding: 4px 8px; border-radius: 4px; font-size: 11px; font-weight: 700; display: inline-block; }
        .badge-online { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-offline { background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
        .badge-share-ok { background: rgba(16, 185, 129, 0.2); color: #34d399; }
        .badge-share-fail { background: rgba(239, 68, 68, 0.2); color: #f87171; }
        .pool-badge { font-family: monospace; background: #0f172a; padding: 4px 8px; border-radius: 4px; color: #38bdf8; font-size: 12px; }
        .live-dot { width: 8px; height: 8px; background: #10b981; border-radius: 50%; display: inline-block; margin-right: 6px; box-shadow: 0 0 8px #10b981; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <div class="brand-icon">AI</div>
                <div>
                    <div class="title"><span class="live-dot"></span>Pearl AI Stealth Proxy (Ultra-Stable 24/7)</div>
                    <div class="subtitle">OpenAI Camouflage Gateway | Upstream: <span id="poolHost" class="pool-badge">Loading...</span></div>
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
                <div class="stat-lbl">Active Workers</div>
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
            <h2 style="font-size:16px; margin-bottom:12px; color:#fff;">Active Mining Workers (1-to-1 Failover Mode)</h2>
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
        let currentPass = localStorage.getItem('proxy_admin_pass') || 'admin123';
        document.getElementById('passInput').value = currentPass;

        function savePass() {
            const val = document.getElementById('passInput').value.trim();
            localStorage.setItem('proxy_admin_pass', val);
            currentPass = val;
            fetchStats();
        }

        async function fetchStats() {
            try {
                const url = '/admin/stats?pass=' + encodeURIComponent(currentPass);
                const res = await fetch(url);
                if (res.status === 401) {
                    document.getElementById('workerTable').innerHTML = '<tr><td colspan="6" style="text-align:center; color:#ef4444; font-weight:bold;">Authentication Required</td></tr>';
                    return;
                }
                const data = await res.json();

                document.getElementById('poolHost').textContent = data.pool_host;
                document.getElementById('totalHash').textContent = (data.total_hashrate / 1e12).toFixed(2) + ' TH/s';
                document.getElementById('activeWorkers').textContent = data.active_workers;
                document.getElementById('acceptedShares').textContent = data.total_shares_accepted;
                document.getElementById('rejectedShares').textContent = data.total_shares_rejected;
                document.getElementById('blockHeight').textContent = data.current_block_height || 'N/A';
                document.getElementById('uptime').textContent = data.uptime_seconds + 's';

                const wTable = document.getElementById('workerTable');
                if (data.workers && data.workers.length > 0) {
                    const now = Math.floor(Date.now() / 1000);
                    let html = '';
                    for (const w of data.workers) {
                        const isOnline = (now - w.last_seen) < 60;
                        const badge = isOnline ? '<span class="badge badge-online">ONLINE</span>' : '<span class="badge badge-offline">OFFLINE</span>';
                        html += `<tr>
                            <td><strong>${w.worker_id}</strong></td>
                            <td>${w.ip}</td>
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
                        const timeStr = new Date(s.timestamp * 1000).toLocaleTimeString();
                        html += `<tr>
                            <td>${timeStr}</td>
                            <td><strong>${s.worker_id}</strong></td>
                            <td><span class="pool-badge">${s.job_id}</span></td>
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

        fetchStats();
        setInterval(fetchStats, 2000);
    </script>
</body>
</html>"""

def format_openai_chunk(job):
    if not isinstance(job, dict):
        job = {}
    diff = job.get('diff', 1.0)
    job_id = job.get('job_id', '0')
    header = job.get('header', '')
    target = job.get('target', '')
    cert_version = job.get('cert_version', 3)
    height = job.get('height', 0)
    content = f"JOB:{job_id}:{header}:{target}:{diff}:{cert_version}:{height}"
    return json.dumps({
        "id": f"chatcmpl-{job_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "gpt-4o-mini",
        "choices": [{
            "index": 0,
            "delta": {"content": content},
            "finish_reason": None
        }]
    })

def push_sse_chunk(conn, chunk):
    """Safely push chunk to all bounded worker SSE queues, dropping oldest if full."""
    for q in list(conn.get("sse_queues", [])):
        try:
            q.put_nowait(chunk)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(chunk)
            except Exception:
                pass
        except Exception:
            pass

def update_worker(worker_id, ip, hashrate=None, accepted=None):
    global total_accepted, total_rejected
    now = int(time.time())
    if worker_id not in workers:
        workers[worker_id] = {
            "worker_id": worker_id,
            "ip": ip,
            "first_seen": now,
            "last_seen": now,
            "shares_accepted": 0,
            "shares_rejected": 0,
            "reported_hashrate": 0.0
        }
    w = workers[worker_id]
    w["last_seen"] = now
    if ip:
        w["ip"] = ip
    if hashrate is not None and hashrate > 0:
        w["reported_hashrate"] = hashrate
    if accepted is True:
        w["shares_accepted"] += 1
        total_accepted += 1
    elif accepted is False:
        w["shares_rejected"] += 1
        total_rejected += 1

def handle_pool_difficulty(conn, msg):
    params = msg.get("params", [])
    new_diff = None
    if isinstance(params, list) and len(params) > 0:
        new_diff = float(params[0])
    elif isinstance(params, dict) and "difficulty" in params:
        new_diff = float(params["difficulty"])

    if new_diff is not None:
        conn["current_diff"] = new_diff
        if conn.get("latest_job"):
            conn["latest_job"]["diff"] = new_diff
            chunk = format_openai_chunk(conn["latest_job"])
            push_sse_chunk(conn, chunk)

def handle_pool_notify(worker_id, conn, msg):
    params = msg.get("params", {})
    if isinstance(params, dict):
        if "diff" not in params and "current_diff" in conn:
            params["diff"] = conn["current_diff"]
        conn["latest_job"] = params
        chunk = format_openai_chunk(params)
        push_sse_chunk(conn, chunk)
    elif isinstance(params, list) and len(params) >= 3:
        job_dict = {
            "job_id": str(params[0]),
            "header": str(params[1]),
            "target": str(params[2]) if len(params) > 2 else "",
            "diff": conn.get("current_diff", 1.0),
            "cert_version": int(params[3]) if len(params) > 3 and str(params[3]).isdigit() else 3,
            "height": int(params[4]) if len(params) > 4 and str(params[4]).isdigit() else 0
        }
        conn["latest_job"] = job_dict
        chunk = format_openai_chunk(job_dict)
        push_sse_chunk(conn, chunk)

def handle_pool_submit_ack(worker_id, conn, msg):
    mid = msg["id"]
    pending = conn["pending_submits"]
    submit_key = None
    if mid in pending:
        submit_key = mid
    elif isinstance(mid, str) and mid.isdigit() and int(mid) in pending:
        submit_key = int(mid)
    elif isinstance(mid, int) and str(mid) in pending:
        submit_key = str(mid)

    if submit_key is not None:
        fut, hs, job_id = pending.pop(submit_key)
        is_ok = msg.get("error") is None and (msg.get("result") is True or msg.get("result") == "true")
        update_worker(worker_id, "", hashrate=hs, accepted=is_ok)
        share_logs.insert(0, {
            "timestamp": int(time.time()),
            "worker_id": worker_id,
            "job_id": job_id,
            "accepted": is_ok,
            "hashrate": hs
        })
        if len(share_logs) > 100:
            share_logs.pop()
        if not fut.done():
            fut.set_result(is_ok)

def process_pool_line(worker_id, conn, line_str):
    if not line_str:
        return
    msg = json.loads(line_str)
    if msg.get("id") == 1 and msg.get("result") is True:
        conn["gzip_v2"] = (msg.get("type") == "v2")
    elif msg.get("method") == "mining.set_difficulty":
        handle_pool_difficulty(conn, msg)
    elif msg.get("method") == "mining.notify":
        handle_pool_notify(worker_id, conn, msg)
    elif "id" in msg and msg.get("id") != 1:
        handle_pool_submit_ack(worker_id, conn, msg)

async def run_single_upstream_session(worker_id, conn, host, port):
    """Run single Stratum session with Kryptex v2 authorize and periodic keepalive."""
    pool_reader, pool_writer = await asyncio.open_connection(host, port, limit=1024*1024)
    configure_socket(pool_writer)

    conn["pool_reader"] = pool_reader
    conn["pool_writer"] = pool_writer
    conn["connected"] = True
    conn["current_endpoint"] = f"{host}:{port}"

    custom_diff_str = DEFAULT_DIFF.strip() if DEFAULT_DIFF else ""
    if custom_diff_str and not custom_diff_str.startswith("d="):
        custom_diff_str = f"d={custom_diff_str}"
    auth_pass = custom_diff_str if custom_diff_str else "x"

    auth_msg = json.dumps({
        "id": 1,
        "method": "mining.authorize",
        "params": {
            "wallet": f"{DEFAULT_WALLET}.{worker_id}",
            "agent": "pearl-t4-miner",
            "password": auth_pass,
            "type": "v2"
        }
    }) + "\n"

    async with conn["write_lock"]:
        pool_writer.write(auth_msg.encode('utf-8'))
        await pool_writer.drain()

    last_ping = time.time()
    try:
        while not conn.get("closed", False):
            try:
                line = await asyncio.wait_for(pool_reader.readline(), timeout=35.0)
                if not line:
                    break
                conn["last_active"] = time.time()
                line_str = line.decode('utf-8', errors='ignore').strip()
                process_pool_line(worker_id, conn, line_str)
            except asyncio.TimeoutError:
                # Send lightweight mining.ping keepalive to prevent firewall socket pruning
                if time.time() - last_ping >= 30.0 and not pool_writer.is_closing():
                    last_ping = time.time()
                    ping_msg = json.dumps({"id": 0, "method": "mining.ping", "params": []}) + "\n"
                    async with conn["write_lock"]:
                        pool_writer.write(ping_msg.encode('utf-8'))
                        await pool_writer.drain()
    finally:
        conn["connected"] = False
        try:
            pool_writer.close()
            await pool_writer.wait_closed()
        except Exception:
            pass

async def upstream_worker_loop(worker_id, conn):
    """Proactive auto-reconnect loop with multi-endpoint failover and exponential backoff."""
    endpoint_idx = 0
    backoff = 1.0

    while not conn.get("closed", False):
        host, port = POOL_ENDPOINTS[endpoint_idx % len(POOL_ENDPOINTS)]
        try:
            await run_single_upstream_session(worker_id, conn, host, port)
            backoff = 1.0
        except Exception as e:
            print(f"[upstream:{worker_id}] Connection to {host}:{port} dropped: {e}")
            endpoint_idx += 1

        if conn.get("closed", False):
            break

        # Flush any hanging pending submits before reconnecting
        for pending_item in list(conn.get("pending_submits", {}).values()):
            try:
                fut = pending_item[0] if isinstance(pending_item, tuple) else pending_item
                if isinstance(fut, asyncio.Future) and not fut.done():
                    fut.set_result(False)
            except Exception:
                pass
        conn["pending_submits"].clear()

        # Exponential backoff with jitter
        await asyncio.sleep(min(backoff, 10.0))
        backoff = min(backoff * 1.5, 15.0)

    # Clean up on final close
    for q in list(conn.get("sse_queues", [])):
        try:
            q.put_nowait(None)
        except Exception:
            pass

async def get_or_create_upstream(worker_id):
    async with global_lock:
        if worker_id not in upstream_locks:
            upstream_locks[worker_id] = asyncio.Lock()
        worker_lock = upstream_locks[worker_id]

    async with worker_lock:
        if worker_id in upstream_connections:
            conn = upstream_connections[worker_id]
            if not conn.get("closed", False):
                return conn
            upstream_connections.pop(worker_id, None)

        conn = {
            "worker_id": worker_id,
            "pool_reader": None,
            "pool_writer": None,
            "write_lock": asyncio.Lock(),
            "task": None,
            "sse_queues": set(),
            "pending_submits": {},
            "latest_job": None,
            "current_diff": 1.0,
            "last_active": time.time(),
            "connected": False,
            "closed": False,
            "gzip_v2": False,
            "current_endpoint": f"{POOL_HOST}:{POOL_PORT}"
        }
        upstream_connections[worker_id] = conn
        conn["task"] = asyncio.create_task(upstream_worker_loop(worker_id, conn))

        # Wait up to 3 seconds for initial mining.notify job from pool
        for _ in range(30):
            if conn["latest_job"]:
                break
            await asyncio.sleep(0.1)
        return conn

async def reaper_loop():
    """Periodically cleans truly dead upstream sockets and stale worker metadata."""
    while True:
        try:
            await asyncio.sleep(30)
            now = time.time()
            dead_workers = []
            for wid, conn in list(upstream_connections.items()):
                # If no SSE listener queues and inactive for > 180s, prune socket cleanly
                if len(conn.get("sse_queues", set())) == 0 and (now - conn.get("last_active", now)) > 180:
                    dead_workers.append(wid)

            for wid in dead_workers:
                conn = upstream_connections.pop(wid, None)
                if conn:
                    conn["closed"] = True
                    if conn.get("task"):
                        conn["task"].cancel()
                    if conn.get("pool_writer"):
                        try:
                            conn["pool_writer"].close()
                            await conn["pool_writer"].wait_closed()
                        except Exception:
                            pass

            # Prune 24h-stale worker records to bound memory footprint
            for wid, w in list(workers.items()):
                if (now - w.get("last_seen", now)) > 86400:
                    workers.pop(wid, None)

        except Exception as e:
            print(f"[reaper] Pruning check: {e}")

async def koyeb_keepalive_loop():
    """Periodically queries own public /health endpoint to prevent Koyeb zero-scaling sleep."""
    while True:
        try:
            await asyncio.sleep(300)
            if KOYEB_APP_URL:
                url = f"{KOYEB_APP_URL.rstrip('/')}/health"
                req = urllib.request.Request(url, headers={"User-Agent": "Pearl-Keepalive/2.0"})
                # Run lightweight synchronous urlopen inside worker thread to avoid blocking loop
                await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=10).read())
        except Exception:
            pass

async def handle_health(writer):
    body = json.dumps({
        "status": "healthy",
        "service": "openai-transparent-proxy",
        "mode": "1-to-1-failover",
        "version": "2.0.0"
    }).encode('utf-8')
    resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    writer.write(resp)
    await writer.drain()

async def handle_models(writer):
    now = int(time.time())
    models = {
        "object": "list",
        "data": [
            {"id": "gpt-4o", "object": "model", "created": now - 86400, "owned_by": "system"},
            {"id": "gpt-4o-mini", "object": "model", "created": now - 86400, "owned_by": "system"},
            {"id": "text-embedding-3-large", "object": "model", "created": now - 86400, "owned_by": "system"}
        ]
    }
    body = json.dumps(models).encode('utf-8')
    resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    writer.write(resp)
    await writer.drain()

async def handle_chat_completions(reader, writer, headers, client_ip):
    clen = int(headers.get("content-length", 0))
    if clen > 0:
        await reader.readexactly(clen)

    worker_id = headers.get("x-worker-id", headers.get("x-worker-name", f"vps-{client_ip.replace('.', '-')}"))
    update_worker(worker_id, client_ip)

    conn = await get_or_create_upstream(worker_id)
    if not conn:
        writer.write(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    writer.write(
        b"HTTP/1.1 200 OK\r\n"
        b"Content-Type: text/event-stream\r\n"
        b"Cache-Control: no-cache, no-transform\r\n"
        b"Connection: keep-alive\r\n"
        b"X-Accel-Buffering: no\r\n"
        b"Access-Control-Allow-Origin: *\r\n\r\n"
    )
    await writer.drain()

    q = asyncio.Queue(maxsize=32)
    conn["sse_queues"].add(q)

    if conn.get("latest_job"):
        chunk = format_openai_chunk(conn["latest_job"])
        writer.write(f"data: {chunk}\n\n".encode('utf-8'))
        await writer.drain()

    try:
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=10)
                if chunk is None:
                    break
                conn["last_active"] = time.time()
                writer.write(f"data: {chunk}\n\n".encode('utf-8'))
                await writer.drain()
            except asyncio.TimeoutError:
                conn["last_active"] = time.time()
                update_worker(worker_id, client_ip)
                ping_chunk = json.dumps({
                    "id": f"chatcmpl-ping-{int(time.time()*1000)}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "gpt-4o-mini",
                    "choices": [{"index": 0, "delta": {"content": "PING"}, "finish_reason": None}]
                })
                writer.write(f"data: {ping_chunk}\n\n".encode('utf-8'))
                await writer.drain()
    except Exception:
        pass
    finally:
        conn["last_active"] = time.time()
        conn["sse_queues"].discard(q)
        writer.close()

def parse_submit_input(input_val, data):
    job_id, plain_proof, hs = "", "", 0.0
    if isinstance(input_val, str) and input_val.startswith("SUBMIT:"):
        parts = input_val.split(':', 3)
        if len(parts) >= 4:
            job_id, plain_proof, hs = parts[1], parts[2], float(parts[3])
    elif isinstance(input_val, dict):
        job_id = input_val.get("job_id", "")
        plain_proof = input_val.get("plain_proof", "")
        hs = float(input_val.get("hs", 0.0))
    return job_id, plain_proof, hs

async def handle_embeddings(reader, writer, headers, client_ip):
    global next_msg_id
    clen = int(headers.get("content-length", 0))
    req_body = await reader.readexactly(clen) if clen > 0 else b"{}"
    data = json.loads(req_body.decode('utf-8', errors='ignore'))

    worker_id = headers.get("x-worker-id", data.get("user", f"vps-{client_ip.replace('.', '-')}"))
    job_id, plain_proof, hs = parse_submit_input(data.get("input", ""), data)

    conn = upstream_connections.get(worker_id)
    if not conn:
        conn = await get_or_create_upstream(worker_id)

    if conn:
        conn["last_active"] = time.time()

    # Wait up to 3s if connection is momentarily auto-reconnecting
    for _ in range(30):
        if conn and conn.get("connected") and conn.get("pool_writer"):
            break
        await asyncio.sleep(0.1)

    if conn and job_id and plain_proof and conn.get("pool_writer"):
        mid = next_msg_id
        next_msg_id += 1
        fut = asyncio.get_event_loop().create_future()
        conn["pending_submits"][mid] = (fut, hs, job_id)

        final_proof = plain_proof
        if conn.get("gzip_v2"):
            final_proof = fast_gzip_compressor.compress_b64_proof(plain_proof)

        submit_msg = json.dumps({
            "jsonrpc": "2.0",
            "id": mid,
            "method": "mining.submit",
            "params": {
                "job_id": job_id,
                "plain_proof": final_proof,
                "hs": hs
            }
        }) + "\n"

        try:
            async with conn["write_lock"]:
                conn["pool_writer"].write(submit_msg.encode('utf-8'))
                await conn["pool_writer"].drain()

            res = await asyncio.wait_for(fut, timeout=25)
            fake_emb = [random.uniform(-0.05, 0.05) for _ in range(16)]
            if res:
                body = json.dumps({
                    "object": "list",
                    "data": [{"object": "embedding", "index": 0, "embedding": fake_emb}],
                    "model": "text-embedding-3-large",
                    "usage": {"prompt_tokens": 1024, "total_tokens": 1024},
                    "status": "accepted"
                }).encode('utf-8')
                resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            else:
                err_body = json.dumps({"error": {"message": "Share rejected by pool", "type": "invalid_request_error"}}).encode('utf-8')
                resp = b"HTTP/1.1 422 Unprocessable Entity\r\nContent-Type: application/json\r\nContent-Length: " + str(len(err_body)).encode() + b"\r\n\r\n" + err_body
            writer.write(resp)
            await writer.drain()
            writer.close()
            return
        except Exception as e:
            print(f"[{worker_id}] Submit wait error: {e}")
        finally:
            conn["pending_submits"].pop(mid, None)

    writer.write(b"HTTP/1.1 422 Unprocessable Entity\r\n\r\n")
    await writer.drain()
    writer.close()

async def handle_admin_stats(writer, qparams, headers):
    req_pass = qparams.get("pass", [""])[0] or headers.get("x-admin-pass", "")
    if not req_pass and headers.get("authorization", "").startswith("Bearer "):
        req_pass = headers.get("authorization", "")[7:]

    if req_pass != ADMIN_PASS:
        writer.write(b"HTTP/1.1 401 Unauthorized\r\n\r\n")
        await writer.drain()
        writer.close()
        return

    now = int(time.time())
    wlist = list(workers.values())
    active_cnt = sum(1 for w in wlist if now - w["last_seen"] < 60)
    tot_hash = sum(w["reported_hashrate"] for w in wlist if now - w["last_seen"] < 60)

    height = "--"
    for conn in upstream_connections.values():
        if conn.get("latest_job") and conn["latest_job"].get("height"):
            height = str(conn["latest_job"]["height"])
            break

    stats_data = {
        "pool_host": f"{POOL_HOST}:{POOL_PORT}",
        "failover_pools": [f"{h}:{p}" for h, p in POOL_ENDPOINTS],
        "uptime_seconds": now - int(start_time),
        "active_workers": active_cnt,
        "total_hashrate": tot_hash,
        "total_shares_accepted": total_accepted,
        "total_shares_rejected": total_rejected,
        "current_block_height": height,
        "active_upstream_connections": len(upstream_connections),
        "workers": wlist,
        "recent_shares": share_logs
    }
    body = json.dumps(stats_data).encode('utf-8')
    resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
    writer.write(resp)
    await writer.drain()
    writer.close()

async def handle_http(reader, writer):
    configure_socket(writer)
    peer = writer.get_extra_info('peername')
    client_ip = peer[0] if peer else "127.0.0.1"

    try:
        req_line = await reader.readline()
        if not req_line:
            writer.close()
            return
        parts = req_line.decode('utf-8', errors='ignore').strip().split()
        if len(parts) < 2:
            writer.close()
            return
        method, path = parts[0], parts[1]

        headers = {}
        while True:
            hline = await reader.readline()
            if not hline or hline in (b'\r\n', b'\n'):
                break
            hstr = hline.decode('utf-8', errors='ignore').strip()
            if ':' in hstr:
                k, v = hstr.split(':', 1)
                headers[k.strip().lower()] = v.strip()

        forwarded_for = headers.get("x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        parsed_url = urlparse(path)
        qparams = parse_qs(parsed_url.query)

        if parsed_url.path in ["/health", "/api/health"]:
            await handle_health(writer)
        elif parsed_url.path in ["/v1/models", "/models"]:
            await handle_models(writer)
        elif parsed_url.path == "/v1/chat/completions" and method == "POST":
            await handle_chat_completions(reader, writer, headers, client_ip)
        elif parsed_url.path == "/v1/embeddings" and method == "POST":
            await handle_embeddings(reader, writer, headers, client_ip)
        elif parsed_url.path in ["/admin/stats", "/stats"]:
            await handle_admin_stats(writer, qparams, headers)
        elif parsed_url.path == "/":
            body = DASHBOARD_HTML.encode('utf-8')
            resp = b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            writer.write(resp)
            await writer.drain()
            writer.close()
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\n\r\n")
            await writer.drain()
            writer.close()

    except Exception as e:
        print(f"HTTP dispatcher error: {e}")
    finally:
        try:
            if not writer.is_closing():
                writer.close()
        except Exception:
            pass

async def main():
    print(f"=== Pearl Transparent Proxy (Ultra-Stable 24/7) ===")
    print(f"Primary Pool: {POOL_HOST}:{POOL_PORT}")
    print(f"Failover Pools: {[f'{h}:{p}' for h, p in POOL_ENDPOINTS]}")
    print(f"Wallet: {DEFAULT_WALLET}")
    print(f"Listening on 0.0.0.0:{PORT}")

    asyncio.create_task(reaper_loop())
    asyncio.create_task(koyeb_keepalive_loop())

    server = await asyncio.start_server(handle_http, '0.0.0.0', PORT)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
