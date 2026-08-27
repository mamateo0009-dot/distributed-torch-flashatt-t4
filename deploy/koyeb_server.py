import asyncio
import json
import time
import os
import random
from urllib.parse import parse_qs, urlparse

POOL_HOST = os.environ.get("POOL_HOST", "pearl-eu1.luckypool.io")
POOL_PORT = int(os.environ.get("POOL_PORT", "3360"))
DEFAULT_WALLET = os.environ.get("WALLET", "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06")
DEFAULT_WORKER = os.environ.get("WORKER", "koyeb-hub")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
PORT = int(os.environ.get("PORT", "8000"))

start_time = time.time()
latest_job = None
active_sse_queues = set()
workers = {}
total_accepted = 0
total_rejected = 0
share_logs = []
upstream_writer = None
pending_submits = {}
next_msg_id = 10

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pearl AI Stealth Proxy & Miner Hub (Koyeb Cloud)</title>
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
                    <div class="title"><span class="live-dot"></span>Pearl AI Stealth Proxy & Miner Hub (Koyeb Cloud)</div>
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
                    document.getElementById('workerTable').innerHTML = '<tr><td colspan="6" style="text-align:center; color:#ef4444; font-weight:bold;">Authentication Required: Enter Admin Password Above</td></tr>';
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

                // Workers
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

                // Recent Shares
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
    content = f"JOB:{job['job_id']}:{job['header']}:{job['target']}:{job['diff']}:{job['cert_version']}:{job.get('height', 0)}"
    return json.dumps({
        "id": f"chatcmpl-{job['job_id']}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": "gpt-4o-mini",
        "choices": [{
            "index": 0,
            "delta": {"content": content},
            "finish_reason": None
        }]
    })

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
    w["ip"] = ip
    if hashrate is not None and hashrate > 0:
        w["reported_hashrate"] = hashrate
    if accepted is True:
        w["shares_accepted"] += 1
        total_accepted += 1
    elif accepted is False:
        w["shares_rejected"] += 1
        total_rejected += 1

async def upstream_client_loop():
    global latest_job, upstream_writer
    while True:
        print(f"[upstream] Connecting to {POOL_HOST}:{POOL_PORT}...")
        try:
            reader, writer = await asyncio.open_connection(POOL_HOST, POOL_PORT)
            upstream_writer = writer
            print(f"[upstream] Connected to {POOL_HOST}:{POOL_PORT}")

            # Send mining.authorize
            auth_msg = json.dumps({
                "jsonrpc": "2.0",
                "id": 1,
                "method": "mining.authorize",
                "params": {
                    "wallet": DEFAULT_WALLET,
                    "worker": DEFAULT_WORKER,
                    "agent": "cpminer/1.0"
                }
            }) + "\n"
            writer.write(auth_msg.encode('utf-8'))
            await writer.drain()
            print(f"[upstream] Sent mining.authorize")

            while True:
                line = await reader.readline()
                if not line:
                    print("[upstream] Pool disconnected")
                    break
                line_str = line.decode('utf-8', errors='ignore').strip()
                if not line_str:
                    continue
                try:
                    msg = json.loads(line_str)
                    if msg.get("method") == "mining.notify":
                        params = msg.get("params", {})
                        latest_job = params
                        print(f"[upstream] New job: {params.get('job_id')} height={params.get('height')}")
                        chunk = format_openai_chunk(params)
                        for q in list(active_sse_queues):
                            try:
                                q.put_nowait(chunk)
                            except Exception:
                                pass

                    elif "id" in msg:
                        mid = msg["id"]
                        submit_key = None
                        if mid in pending_submits:
                            submit_key = mid
                        elif isinstance(mid, str) and mid.isdigit() and int(mid) in pending_submits:
                            submit_key = int(mid)
                        elif isinstance(mid, int) and str(mid) in pending_submits:
                            submit_key = str(mid)

                        if submit_key is not None:
                            fut, worker_id, hs, job_id = pending_submits.pop(submit_key)
                            is_ok = msg.get("error") is None and (msg.get("result") is True or msg.get("result") == "true")
                            print(f"[upstream] Submit ack for {worker_id}: ok={is_ok} (raw={msg})")
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
                except Exception as e:
                    print(f"[upstream] Parse error: {e}")
        except Exception as e:
            print(f"[upstream] Connection error: {e}")
        upstream_writer = None
        await asyncio.sleep(3)

async def handle_http(reader, writer):
    global next_msg_id
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
            if not hline or hline == b'\r\n' or hline == b'\n':
                break
            hstr = hline.decode('utf-8', errors='ignore').strip()
            if ':' in hstr:
                k, v = hstr.split(':', 1)
                headers[k.strip().lower()] = v.strip()

        parsed_url = urlparse(path)
        qparams = parse_qs(parsed_url.query)

        # 1. Health check
        if parsed_url.path in ["/health", "/api/health"]:
            body = json.dumps({"status": "healthy", "service": "openai-inference-gateway"}).encode('utf-8')
            resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            writer.write(resp)
            await writer.drain()

        # 2. Models list
        elif parsed_url.path in ["/v1/models", "/models"]:
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

        # 3. Chat completions (SSE Stream for Jobs)
        elif parsed_url.path == "/v1/chat/completions" and method == "POST":
            clen = int(headers.get("content-length", 0))
            req_body = await reader.read(clen) if clen > 0 else b""
            worker_id = headers.get("x-worker-id", headers.get("x-worker-name", f"vps-{client_ip.replace('.', '-')}"))
            update_worker(worker_id, client_ip)

            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: text/event-stream\r\n"
                b"Cache-Control: no-cache\r\n"
                b"Connection: keep-alive\r\n"
                b"Access-Control-Allow-Origin: *\r\n\r\n"
            )
            await writer.drain()

            q = asyncio.Queue()
            active_sse_queues.add(q)

            if latest_job:
                chunk = format_openai_chunk(latest_job)
                writer.write(f"data: {chunk}\n\n".encode('utf-8'))
                await writer.drain()

            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(q.get(), timeout=15)
                        writer.write(f"data: {chunk}\n\n".encode('utf-8'))
                        await writer.drain()
                    except asyncio.TimeoutError:
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
                active_sse_queues.discard(q)

        # 4. Embeddings (Submit Share)
        elif parsed_url.path == "/v1/embeddings" and method == "POST":
            clen = int(headers.get("content-length", 0))
            req_body = await reader.readexactly(clen) if clen > 0 else b"{}"
            data = json.loads(req_body.decode('utf-8', errors='ignore'))

            worker_id = headers.get("x-worker-id", data.get("user", f"vps-{client_ip.replace('.', '-')}"))
            input_val = data.get("input", "")

            job_id, plain_proof, hs = "", "", 0.0
            if isinstance(input_val, str) and input_val.startswith("SUBMIT:"):
                parts = input_val.split(':', 3)
                if len(parts) >= 4:
                    job_id, plain_proof, hs = parts[1], parts[2], float(parts[3])
            elif isinstance(input_val, dict):
                job_id = input_val.get("job_id", "")
                plain_proof = input_val.get("plain_proof", "")
                hs = float(input_val.get("hs", 0.0))

            if upstream_writer and job_id and plain_proof:
                mid = next_msg_id
                next_msg_id += 1
                fut = asyncio.get_event_loop().create_future()
                pending_submits[mid] = (fut, worker_id, hs, job_id)

                submit_msg = json.dumps({
                    "jsonrpc": "2.0",
                    "id": mid,
                    "method": "mining.submit",
                    "params": {
                        "job_id": job_id,
                        "plain_proof": plain_proof,
                        "hs": hs
                    }
                }) + "\n"
                upstream_writer.write(submit_msg.encode('utf-8'))
                await upstream_writer.drain()

                try:
                    res = await asyncio.wait_for(fut, timeout=25)
                    fake_emb = [random.uniform(-0.05, 0.05) for _ in range(16)]
                    body = json.dumps({
                        "object": "list",
                        "data": [{"object": "embedding", "index": 0, "embedding": fake_emb}],
                        "model": "text-embedding-3-large",
                        "usage": {"prompt_tokens": 1024, "total_tokens": 1024},
                        "status": "accepted" if res else "rejected"
                    }).encode('utf-8')
                    resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
                    writer.write(resp)
                    await writer.drain()
                    writer.close()
                    return
                except Exception as e:
                    print(f"[submit] Wait error: {e}")

            writer.write(b"HTTP/1.1 422 Unprocessable Entity\r\n\r\n")
            await writer.drain()

        # 5. Stats API
        elif parsed_url.path in ["/admin/stats", "/stats"]:
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

            stats_data = {
                "pool_host": POOL_HOST,
                "default_wallet": DEFAULT_WALLET,
                "uptime_seconds": int(now - start_time),
                "active_workers": active_cnt,
                "total_shares_accepted": total_accepted,
                "total_shares_rejected": total_rejected,
                "total_hashrate": tot_hash,
                "current_job_id": latest_job.get("job_id") if latest_job else None,
                "current_block_height": latest_job.get("height") if latest_job else None,
                "workers": wlist,
                "recent_shares": share_logs
            }
            body = json.dumps(stats_data).encode('utf-8')
            resp = b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nAccess-Control-Allow-Origin: *\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            writer.write(resp)
            await writer.drain()

        # 6. Dashboard HTML
        else:
            body = DASHBOARD_HTML.encode('utf-8')
            resp = b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body
            writer.write(resp)
            await writer.drain()

    except Exception as e:
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass

async def main():
    server = await asyncio.start_server(handle_http, '0.0.0.0', PORT)
    print(f"Proxy HTTP server listening on http://0.0.0.0:{PORT}")
    asyncio.create_task(upstream_client_loop())
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
