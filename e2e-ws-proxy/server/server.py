#!/usr/bin/env python3
"""
E2E Encrypted WebSocket Stealth Proxy Server for Pearl ZK-PoW Mining.

Features:
- Dual-mode server: Encrypted WebSocket Tunnel + Camouflage OpenAI HTTP Endpoints + Realtime Web Dashboard.
- Zero external dependencies option (supports pure asyncio / websockets / aiohttp).
- 1-to-1 transparent upstream TCP Stratum connection per worker node.
- Real-time E2E encryption via ChaCha20-Poly1305 / HMAC-CTR.
"""

import asyncio
import json
import time
import os
import sys
import struct
import hashlib
import hmac
import secrets
from urllib.parse import parse_qs, urlparse

# Ensure common crypto is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.crypto import E2ECipher, DEFAULT_SECRET_KEY

# Configuration
POOL_HOST = os.environ.get("POOL_HOST", "prl.kryptex.network")
POOL_PORT = int(os.environ.get("POOL_PORT", "7048"))
DEFAULT_WALLET = os.environ.get("WALLET", "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06")
ADMIN_PASS = os.environ.get("ADMIN_PASS", "admin123")
E2E_SECRET = os.environ.get("E2E_SECRET", DEFAULT_SECRET_KEY)
PORT = int(os.environ.get("PORT", "8000"))

start_time = time.time()
workers = {}
total_accepted = 0
total_rejected = 0
share_logs = []
cipher = E2ECipher(E2E_SECRET)

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Pearl E2E WebSocket Stealth Proxy</title>
    <style>
        :root {
            --bg: #07090e;
            --card-bg: rgba(15, 23, 42, 0.85);
            --card-border: rgba(56, 189, 248, 0.2);
            --primary: #38bdf8;
            --primary-glow: rgba(56, 189, 248, 0.4);
            --accent: #10b981;
            --danger: #ef4444;
            --text: #f8fafc;
            --text-dim: #94a3b8;
        }
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
            background: var(--bg);
            color: var(--text);
            min-height: 100vh;
            padding: 24px;
            background-image: radial-gradient(circle at 50% 0%, #1e1b4b 0%, #07090e 75%);
        }
        .container { max-width: 1200px; margin: 0 auto; }
        header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
        .brand { display: flex; align-items: center; gap: 12px; }
        .brand-icon { width: 40px; height: 40px; background: linear-gradient(135deg, #06b6d4, #6366f1); border-radius: 10px; display: flex; align-items: center; justify-content: center; font-weight: bold; color: #fff; font-size: 18px; box-shadow: 0 0 15px rgba(6,182,212,0.4); }
        .title { font-size: 22px; font-weight: 700; color: #fff; display: flex; align-items: center; gap: 8px; }
        .subtitle { font-size: 13px; color: var(--text-dim); margin-top: 2px; }
        .auth-bar { display: flex; align-items: center; gap: 8px; }
        .pass-input { background: #0f172a; border: 1px solid #334155; color: #fff; padding: 8px 14px; border-radius: 8px; font-size: 13px; outline: none; }
        .btn { background: #0284c7; color: #fff; border: none; padding: 8px 16px; border-radius: 8px; font-weight: 600; cursor: pointer; font-size: 13px; transition: 0.2s; }
        .btn:hover { background: #38bdf8; color: #07090e; }
        .card { background: var(--card-bg); border: 1px solid var(--card-border); border-radius: 14px; padding: 20px; margin-bottom: 20px; backdrop-filter: blur(10px); box-shadow: 0 10px 30px -5px rgba(0,0,0,0.6); }
        .grid-stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; margin-bottom: 24px; }
        .stat-box { background: rgba(30, 41, 59, 0.6); border: 1px solid rgba(255,255,255,0.06); padding: 20px; border-radius: 12px; text-align: center; }
        .stat-val { font-size: 28px; font-weight: 800; color: var(--primary); letter-spacing: -0.5px; text-shadow: 0 0 15px var(--primary-glow); }
        .stat-lbl { font-size: 12px; text-transform: uppercase; color: var(--text-dim); font-weight: 600; margin-top: 6px; letter-spacing: 0.5px; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; }
        th, td { padding: 14px 16px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.06); font-size: 14px; }
        th { background: rgba(30, 41, 59, 0.8); color: var(--text-dim); font-size: 12px; text-transform: uppercase; letter-spacing: 0.5px; font-weight: 600; }
        tr:hover td { background: rgba(255,255,255,0.03); }
        .badge { padding: 4px 8px; border-radius: 6px; font-size: 11px; font-weight: 700; display: inline-block; }
        .badge-online { background: rgba(16, 185, 129, 0.2); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
        .badge-e2e { background: rgba(99, 102, 241, 0.2); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3); }
        .pool-badge { font-family: monospace; background: #0f172a; padding: 4px 8px; border-radius: 6px; color: #38bdf8; font-size: 12px; }
        .live-dot { width: 8px; height: 8px; background: #10b981; border-radius: 50%; display: inline-block; margin-right: 6px; box-shadow: 0 0 8px #10b981; animation: pulse 2s infinite; }
        @keyframes pulse { 0% { opacity: 1; } 50% { opacity: 0.3; } 100% { opacity: 1; } }
    </style>
</head>
<body>
    <div class="container">
        <header>
            <div class="brand">
                <div class="brand-icon">🔒</div>
                <div>
                    <div class="title"><span class="live-dot"></span>Pearl E2E WebSocket Proxy</div>
                    <div class="subtitle">AEAD End-to-End Encrypted Tunnel | Upstream: <span id="poolHost" class="pool-badge">Loading...</span></div>
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
                <div class="stat-val" id="activeNodes">0</div>
                <div class="stat-lbl">Active E2E Workers</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" style="color: var(--accent);" id="accShares">0</div>
                <div class="stat-lbl">Accepted Shares (0% Reject)</div>
            </div>
            <div class="stat-box">
                <div class="stat-val" id="uptime">0m</div>
                <div class="stat-lbl">Proxy Uptime</div>
            </div>
        </div>

        <div class="card">
            <h3 style="font-size: 16px; margin-bottom: 12px; color: #fff;">Connected E2E Workers</h3>
            <table>
                <thead>
                    <tr>
                        <th>Worker ID</th>
                        <th>Wallet</th>
                        <th>Encryption</th>
                        <th>Reported Hashrate</th>
                        <th>Accepted / Rejected</th>
                        <th>Status</th>
                        <th>Last Seen</th>
                    </tr>
                </thead>
                <tbody id="workerTable">
                    <tr><td colspan="7" style="text-align: center; color: var(--text-dim);">No active workers connected.</td></tr>
                </tbody>
            </table>
        </div>
    </div>

    <script>
        let adminPass = localStorage.getItem('proxy_admin_pass') || 'admin123';
        document.getElementById('passInput').value = adminPass;

        function savePass() {
            adminPass = document.getElementById('passInput').value;
            localStorage.setItem('proxy_admin_pass', adminPass);
            fetchStats();
        }

        async function fetchStats() {
            try {
                const res = await fetch(`/api/stats?pass=${encodeURIComponent(adminPass)}`);
                if (!res.ok) return;
                const data = await res.json();
                document.getElementById('poolHost').innerText = `${data.pool_host}:${data.pool_port}`;
                document.getElementById('activeNodes').innerText = data.active_workers;
                document.getElementById('accShares').innerText = `${data.total_accepted} (${data.reject_rate})`;
                document.getElementById('totalHash').innerText = `${data.total_hashrate_th.toFixed(2)} TH/s`;
                document.getElementById('uptime').innerText = data.uptime;

                const tbody = document.getElementById('workerTable');
                if (data.workers.length === 0) {
                    tbody.innerHTML = '<tr><td colspan="7" style="text-align: center; color: var(--text-dim);">No active workers connected.</td></tr>';
                } else {
                    tbody.innerHTML = data.workers.map(w => `
                        <tr>
                            <td style="font-weight: 600; color: #fff;">${w.worker_id}</td>
                            <td style="font-family: monospace; font-size: 12px; color: var(--text-dim);">${w.wallet.slice(0, 10)}...${w.wallet.slice(-6)}</td>
                            <td><span class="badge badge-e2e">ChaCha20-Poly1305</span></td>
                            <td style="font-weight: 700; color: var(--primary);">${w.hashrate_th.toFixed(2)} TH/s</td>
                            <td><span style="color: var(--accent);">${w.accepted}</span> / <span style="color: var(--danger);">${w.rejected}</span></td>
                            <td><span class="badge badge-online">ONLINE</span></td>
                            <td style="color: var(--text-dim);">${w.last_seen}</td>
                        </tr>
                    `).join('');
                }
            } catch (e) {
                console.error(e);
            }
        }

        setInterval(fetchStats, 3000);
        fetchStats();
    </script>
</body>
</html>
"""

# WebSocket Protocol Handlers
def parse_ws_frame(data: bytes):
    """Parses standard RFC 6455 WebSocket frame."""
    if len(data) < 2:
        return None, 0
    b1, b2 = data[0], data[1]
    fin = (b1 & 0x80) != 0
    opcode = b1 & 0x0F
    masked = (b2 & 0x80) != 0
    payload_len = b2 & 0x7F

    offset = 2
    if payload_len == 126:
        if len(data) < 4: return None, 0
        payload_len = struct.unpack(">H", data[2:4])[0]
        offset = 4
    elif payload_len == 127:
        if len(data) < 10: return None, 0
        payload_len = struct.unpack(">Q", data[2:10])[0]
        offset = 10

    mask_key = None
    if masked:
        if len(data) < offset + 4: return None, 0
        mask_key = data[offset:offset+4]
        offset += 4

    if len(data) < offset + payload_len:
        return None, 0

    payload = data[offset:offset+payload_len]
    if masked and mask_key:
        payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))

    return (opcode, payload), offset + payload_len

def make_ws_frame(payload: bytes, opcode: int = 0x02) -> bytes:
    """Encapsulates binary payload into unmasked server WebSocket frame."""
    b1 = 0x80 | (opcode & 0x0F)
    payload_len = len(payload)
    if payload_len <= 125:
        header = struct.pack("!BB", b1, payload_len)
    elif payload_len <= 65535:
        header = struct.pack("!BBH", b1, 126, payload_len)
    else:
        header = struct.pack("!BBQ", b1, 127, payload_len)
    return header + payload

async def handle_e2e_ws_client(reader, writer, worker_id, wallet, path):
    """
    Manages E2E Encrypted WebSocket connection with miner client
    and transparently pipes to Upstream Stratum TCP Pool.
    """
    global total_accepted, total_rejected
    print(f"[E2E-WS] Worker connected: {worker_id} (Wallet: {wallet})", flush=True)

    workers[worker_id] = {
        "worker_id": worker_id,
        "wallet": wallet,
        "hashrate_th": 0.0,
        "accepted": 0,
        "rejected": 0,
        "last_seen_ts": time.time(),
        "connected_at": time.time()
    }

    upstream_reader = None
    upstream_writer = None

    try:
        upstream_reader, upstream_writer = await asyncio.open_connection(POOL_HOST, POOL_PORT)
        print(f"[E2E-WS] Connected to Upstream Stratum: {POOL_HOST}:{POOL_PORT} for {worker_id}", flush=True)
    except Exception as e:
        print(f"[E2E-WS] Failed to connect upstream pool: {e}", file=sys.stderr, flush=True)
        writer.close()
        return

    async def upstream_to_client():
        """Reads Stratum JSON-RPC from Pool, Encrypts E2E, sends via WebSocket."""
        nonlocal upstream_reader, writer
        buffer = ""
        try:
            while True:
                line = await upstream_reader.readline()
                if not line:
                    break
                # Decoded raw stratum message from pool
                msg_str = line.decode('utf-8', errors='ignore')

                # Check for share responses
                try:
                    data = json.loads(msg_str)
                    if "result" in data:
                        if data["result"] is True:
                            workers[worker_id]["accepted"] += 1
                        elif data["result"] is False or data.get("error") is not None:
                            workers[worker_id]["rejected"] += 1
                except Exception:
                    pass

                # Encrypt payload E2E
                encrypted_frame = cipher.encrypt(line)
                ws_frame = make_ws_frame(encrypted_frame, opcode=0x02) # Binary frame
                writer.write(ws_frame)
                await writer.drain()
        except Exception as e:
            pass

    async def client_to_upstream():
        """Reads E2E Encrypted WebSocket frame from Miner, Decrypts, writes to Stratum Pool."""
        nonlocal reader, upstream_writer
        raw_buffer = bytearray()
        try:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                raw_buffer.extend(chunk)
                workers[worker_id]["last_seen_ts"] = time.time()

                while True:
                    frame, consumed = parse_ws_frame(raw_buffer)
                    if frame is None:
                        break
                    raw_buffer = raw_buffer[consumed:]
                    opcode, payload = frame

                    if opcode == 0x08: # Close frame
                        return
                    elif opcode == 0x09: # Ping
                        writer.write(make_ws_frame(payload, opcode=0x0A))
                        await writer.drain()
                        continue
                    elif opcode == 0x02 or opcode == 0x01: # Binary or Text E2E Frame
                        try:
                            # Decrypt E2E payload
                            decrypted = cipher.decrypt(payload)

                            # Parse stratum for telemetry
                            try:
                                msg_json = json.loads(decrypted.decode('utf-8').strip())
                                if msg_json.get("method") == "mining.authorize":
                                    # Substitute wallet if needed
                                    pass
                                elif msg_json.get("method") == "mining.submit":
                                    pass
                            except Exception:
                                pass

                            upstream_writer.write(decrypted)
                            await upstream_writer.drain()
                        except Exception as dec_err:
                            print(f"[E2E-WS] Decrypt error from {worker_id}: {dec_err}", flush=True)
        except Exception as e:
            pass

    try:
        await asyncio.gather(upstream_to_client(), client_to_upstream())
    finally:
        print(f"[E2E-WS] Worker disconnected: {worker_id}", flush=True)
        if worker_id in workers:
            del workers[worker_id]
        if upstream_writer:
            upstream_writer.close()
        writer.close()

async def handle_http_request(reader, writer):
    """Handles HTTP, WebSocket upgrade, OpenAI disguise, and Dashboard."""
    header_data = await reader.readuntil(b"\r\n\r\n")
    header_text = header_data.decode('utf-8', errors='ignore')
    lines = header_text.split("\r\n")
    if not lines:
        writer.close()
        return

    req_line = lines[0].split(" ")
    method = req_line[0]
    path = req_line[1] if len(req_line) > 1 else "/"

    headers = {}
    for line in lines[1:]:
        if ": " in line:
            k, v = line.split(": ", 1)
            headers[k.lower()] = v.strip()

    # Check WebSocket Upgrade
    if headers.get("upgrade", "").lower() == "websocket":
        ws_key = headers.get("sec-websocket-key", "")
        # Compute Sec-WebSocket-Accept
        magic = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
        accept_key = hashlib.sha1(ws_key.encode('utf-8') + magic).digest()
        accept_b64 = secrets.base64.b64encode(accept_key).decode('ascii')

        worker_id = headers.get("x-worker-id") or f"e2e-node-{secrets.token_hex(4)}"
        auth_hdr = headers.get("authorization", "")
        wallet = auth_hdr.replace("Bearer ", "").strip() if auth_hdr else DEFAULT_WALLET

        upgrade_resp = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept_b64}\r\n"
            "Sec-WebSocket-Protocol: e2e-stratum\r\n"
            "\r\n"
        )
        writer.write(upgrade_resp.encode('utf-8'))
        await writer.drain()

        await handle_e2e_ws_client(reader, writer, worker_id, wallet, path)
        return

    # Dashboard & API routes
    if path == "/" or path.startswith("/?"):
        resp = f"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nContent-Length: {len(DASHBOARD_HTML.encode('utf-8'))}\r\n\r\n{DASHBOARD_HTML}"
        writer.write(resp.encode('utf-8'))
        await writer.drain()
        writer.close()
        return

    if path.startswith("/api/stats"):
        parsed = urlparse(path)
        qs = parse_qs(parsed.query)
        pwd = qs.get("pass", [""])[0]
        if pwd != ADMIN_PASS:
            resp_body = json.dumps({"error": "Unauthorized"}).encode('utf-8')
            writer.write(f"HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\nContent-Length: {len(resp_body)}\r\n\r\n".encode('utf-8') + resp_body)
            await writer.drain()
            writer.close()
            return

        worker_list = []
        now = time.time()
        for w in workers.values():
            ago = int(now - w["last_seen_ts"])
            last_seen_str = f"{ago}s ago" if ago < 60 else f"{ago//60}m ago"
            worker_list.append({
                "worker_id": w["worker_id"],
                "wallet": w["wallet"],
                "hashrate_th": w.get("hashrate_th", 0.0),
                "accepted": w.get("accepted", 0),
                "rejected": w.get("rejected", 0),
                "last_seen": last_seen_str
            })

        total_acc = sum(w["accepted"] for w in workers.values()) + total_accepted
        total_rej = sum(w["rejected"] for w in workers.values()) + total_rejected
        tot = total_acc + total_rej
        reject_rate = f"{(total_rej/tot*100):.1f}%" if tot > 0 else "0.0%"

        up_sec = int(now - start_time)
        uptime_str = f"{up_sec//3600}h {(up_sec%3600)//60}m" if up_sec >= 3600 else f"{up_sec//60}m {up_sec%60}s"

        stats_data = {
            "pool_host": POOL_HOST,
            "pool_port": POOL_PORT,
            "active_workers": len(workers),
            "total_accepted": total_acc,
            "total_rejected": total_rej,
            "reject_rate": reject_rate,
            "total_hashrate_th": sum(w.get("hashrate_th", 0.0) for w in workers.values()),
            "uptime": uptime_str,
            "workers": worker_list
        }
        body = json.dumps(stats_data).encode('utf-8')
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode('utf-8') + body)
        await writer.drain()
        writer.close()
        return

    # OpenAI Camouflage Endpoints
    if path == "/v1/models":
        body = json.dumps({
            "object": "list",
            "data": [
                {"id": "gpt-4o", "object": "model", "created": 1787982679, "owned_by": "system"},
                {"id": "gpt-4o-mini", "object": "model", "created": 1787982679, "owned_by": "system"},
                {"id": "text-embedding-3-large", "object": "model", "created": 1787982679, "owned_by": "system"}
            ]
        }).encode('utf-8')
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode('utf-8') + body)
        await writer.drain()
        writer.close()
        return

    # Health check
    if path == "/health":
        body = json.dumps({"status": "healthy", "service": "pearl-e2e-ws-proxy"}).encode('utf-8')
        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n".encode('utf-8') + body)
        await writer.drain()
        writer.close()
        return

    # Default 404
    writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
    await writer.drain()
    writer.close()

async def main():
    server = await asyncio.start_server(handle_http_request, "0.0.0.0", PORT)
    print(f"🚀 Pearl E2E Encrypted WebSocket Proxy running on port {PORT}", flush=True)
    print(f"🔗 Upstream Pool: {POOL_HOST}:{POOL_PORT}", flush=True)
    print(f"🔒 E2E Encryption: Enabled (AEAD ChaCha20-Poly1305 / HMAC-CTR)", flush=True)
    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
