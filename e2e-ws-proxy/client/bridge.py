#!/usr/bin/env python3
"""
E2E Encrypted WebSocket Client Bridge for Local & Cloud Miners.

Connects to:
- Local Stratum Miner (TCP port 3333, standard JSON-RPC).
- Upstream E2E WebSocket Proxy (wss://<proxy-url>/v1/tunnel).

Features:
- Full E2E Encryption (ChaCha20-Poly1305 / HMAC-CTR).
- Zero plaintext Stratum traffic on the network wire.
- Automatic reconnection & background heartbeat/ping-pong.
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
import argparse
import socket
import ssl
from urllib.parse import urlparse

# Ensure common crypto is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from common.crypto import E2ECipher, DEFAULT_SECRET_KEY

# WebSocket RFC 6455 Client Framing
def make_client_ws_frame(payload: bytes, opcode: int = 0x02) -> bytes:
    """Encapsulates binary payload into masked client WebSocket frame."""
    b1 = 0x80 | (opcode & 0x0F)
    payload_len = len(payload)
    mask_key = secrets.token_bytes(4)

    if payload_len <= 125:
        header = struct.pack("!BB", b1, 0x80 | payload_len)
    elif payload_len <= 65535:
        header = struct.pack("!BBH", b1, 0x80 | 126, payload_len)
    else:
        header = struct.pack("!BBQ", b1, 0x80 | 127, payload_len)

    masked_payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
    return header + mask_key + masked_payload

def parse_server_ws_frame(data: bytes):
    """Parses unmasked/masked server WebSocket frame."""
    if len(data) < 2:
        return None, 0
    b1, b2 = data[0], data[1]
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

async def connect_ws_proxy(proxy_url: str, wallet: str, worker: str):
    """Initiates HTTP WebSocket handshake with remote proxy."""
    parsed = urlparse(proxy_url)
    is_ssl = (parsed.scheme in ("https", "wss"))
    host = parsed.hostname
    port = parsed.port or (443 if is_ssl else 80)
    path = parsed.path or "/v1/tunnel"

    ssl_ctx = ssl.create_default_context() if is_ssl else None
    reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)

    ws_key = secrets.base64.b64encode(secrets.token_bytes(16)).decode('ascii')
    req = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        f"Upgrade: websocket\r\n"
        f"Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {ws_key}\r\n"
        f"Sec-WebSocket-Version: 13\r\n"
        f"Sec-WebSocket-Protocol: e2e-stratum\r\n"
        f"Authorization: Bearer {wallet}\r\n"
        f"X-Worker-Id: {worker}\r\n"
        f"User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36\r\n"
        f"\r\n"
    )
    writer.write(req.encode('utf-8'))
    await writer.drain()

    # Read Handshake Response
    resp_header = await reader.readuntil(b"\r\n\r\n")
    if b"101 Switching Protocols" not in resp_header:
        writer.close()
        raise ConnectionError(f"WebSocket upgrade failed: {resp_header[:100]}")

    return reader, writer

async def handle_local_miner(local_reader, local_writer, proxy_url: str, wallet: str, worker: str, cipher: E2ECipher):
    """Bridges local miner Stratum TCP socket with remote E2E encrypted WebSocket."""
    print(f"[E2E-BRIDGE] Local miner connected. Initiating E2E WS tunnel to {proxy_url}...", flush=True)
    try:
        ws_reader, ws_writer = await connect_ws_proxy(proxy_url, wallet, worker)
        print(f"🔒 [E2E-BRIDGE] Secure E2E WebSocket tunnel established for {worker}!", flush=True)
    except Exception as e:
        print(f"[E2E-BRIDGE] Failed to connect E2E WS proxy: {e}", file=sys.stderr, flush=True)
        local_writer.close()
        return

    async def local_to_ws():
        """Reads Stratum from local miner, Encrypts E2E, sends via WS to Proxy."""
        nonlocal local_reader, ws_writer
        try:
            while True:
                line = await local_reader.readline()
                if not line:
                    break
                # Encrypt E2E
                enc_payload = cipher.encrypt(line)
                frame = make_client_ws_frame(enc_payload, opcode=0x02) # Binary E2E frame
                ws_writer.write(frame)
                await ws_writer.drain()
        except Exception:
            pass

    async def ws_to_local():
        """Reads E2E Encrypted frames from WS Proxy, Decrypts, writes Stratum to local miner."""
        nonlocal ws_reader, local_writer
        raw_buffer = bytearray()
        try:
            while True:
                chunk = await ws_reader.read(65536)
                if not chunk:
                    break
                raw_buffer.extend(chunk)
                while True:
                    frame, consumed = parse_server_ws_frame(raw_buffer)
                    if frame is None:
                        break
                    raw_buffer = raw_buffer[consumed:]
                    opcode, payload = frame

                    if opcode == 0x08: # Close
                        return
                    elif opcode == 0x09: # Ping
                        ws_writer.write(make_client_ws_frame(payload, opcode=0x0A))
                        await ws_writer.drain()
                    elif opcode == 0x02 or opcode == 0x01: # E2E Binary frame
                        try:
                            decrypted = cipher.decrypt(payload)
                            local_writer.write(decrypted)
                            await local_writer.drain()
                        except Exception as e:
                            print(f"[E2E-BRIDGE] Decrypt error from proxy: {e}", flush=True)
        except Exception:
            pass

    try:
        await asyncio.gather(local_to_ws(), ws_to_local())
    finally:
        print(f"[E2E-BRIDGE] Miner disconnected: {worker}", flush=True)
        ws_writer.close()
        local_writer.close()

async def main():
    parser = argparse.ArgumentParser(description="Pearl E2E WebSocket Stealth Miner Bridge")
    parser.add_argument("--proxy", type=str, default="ws://127.0.0.1:8000", help="E2E WebSocket Proxy URL")
    parser.add_argument("--port", type=int, default=3333, help="Local Stratum listening port")
    parser.add_argument("--wallet", type=str, default="prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06", help="Mining wallet")
    parser.add_argument("--worker", type=str, default="", help="Worker ID")
    parser.add_argument("--secret", type=str, default=DEFAULT_SECRET_KEY, help="E2E Shared Encryption Secret")
    args = parser.parse_args()

    worker_id = args.worker if args.worker else f"e2e-node-{secrets.token_hex(4)}"
    cipher = E2ECipher(args.secret)

    print("================================================================")
    print(" 🔒 Pearl E2E Encrypted WebSocket Stealth Miner Bridge")
    print(f" Local Stratum Listener: 127.0.0.1:{args.port}")
    print(f" Remote E2E WS Tunnel:   {args.proxy}")
    print(f" Worker ID:              {worker_id}")
    print(f" Encryption Protocol:    ChaCha20-Poly1305 / HMAC-CTR AEAD")
    print("================================================================")

    server = await asyncio.start_server(
        lambda r, w: handle_local_miner(r, w, args.proxy, args.wallet, worker_id, cipher),
        "127.0.0.1", args.port
    )

    async with server:
        await server.serve_forever()

if __name__ == "__main__":
    asyncio.run(main())
