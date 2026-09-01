#!/usr/bin/env python3
import sys
import os
import json
import time
import socket
import threading
import random
import urllib.request
import urllib.parse
import urllib.error
from concurrent.futures import ThreadPoolExecutor

def run_bridge(local_port=3333, proxy_url="http://127.0.0.1:8000", wallet="", worker="vps-node"):
    print(f"=== OpenAI Stealth Bridge v2 (Anti-Detection) ===")
    print(f"Local Stratum Listener: 127.0.0.1:{local_port}")
    print(f"Remote OpenAI Proxy:    {proxy_url}")
    print(f"Worker Tag:             {worker}")

    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('127.0.0.1', local_port))
    server_sock.listen(5)

    threading.Thread(target=background_traffic_chaff, args=(proxy_url, wallet, worker), daemon=True).start()

    while True:
        client_sock, client_addr = server_sock.accept()
        print(f"Miner connected from {client_addr}")
        threading.Thread(target=handle_miner_client, args=(client_sock, proxy_url, wallet, worker), daemon=True).start()

def background_traffic_chaff(proxy_url, wallet, worker):
    while True:
        try:
            time.sleep(random.uniform(45.0, 120.0))
            models_url = f"{proxy_url.rstrip('/')}/v1/models"
            req = urllib.request.Request(
                models_url,
                headers={
                    "Authorization": f"Bearer {wallet}",
                    "X-Worker-Id": worker,
                    "User-Agent": "openai-python/1.35.0"
                }
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                _ = resp.read()
        except Exception:
            pass

def handle_miner_client(client_sock, proxy_url, wallet, worker):
    client_file = client_sock.makefile('rw', buffering=1, encoding='utf-8')
    stop_event = threading.Event()
    sock_lock = threading.Lock()
    submit_executor = ThreadPoolExecutor(max_workers=8)

    def safe_send(data):
        with sock_lock:
            try:
                msg_bytes = data.encode('utf-8') if isinstance(data, str) else data
                client_sock.sendall(msg_bytes)
            except Exception:
                pass

    def stream_jobs():
        current_synced_diff = None
        while not stop_event.is_set():
            try:
                chat_url = f"{proxy_url.rstrip('/')}/v1/chat/completions"
                payload = json.dumps({
                    "model": "gpt-4o-mini",
                    "messages": [
                        {"role": "system", "content": "You are a specialized code optimization assistant."},
                        {"role": "user", "content": f"Sync worker thread context session_{random.randint(1000,9999)}"}
                    ],
                    "stream": True,
                    "temperature": 0.7
                }).encode('utf-8')

                req = urllib.request.Request(
                    chat_url,
                    data=payload,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {wallet}",
                        "X-Worker-Id": worker,
                        "User-Agent": "openai-python/1.35.0"
                    }
                )

                with urllib.request.urlopen(req, timeout=30) as resp:
                    for line in resp:
                        if stop_event.is_set():
                            break
                        line_str = line.decode('utf-8', errors='ignore').strip()
                        if line_str.startswith("data:"):
                            data_part = line_str[5:].strip()
                            if data_part == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_part)
                                if "choices" in chunk and len(chunk["choices"]) > 0:
                                    delta = chunk["choices"][0].get("delta", {})
                                    content = delta.get("content", "")
                                    if content.startswith("JOB:"):
                                        parts = content.split(':')
                                        if len(parts) >= 6:
                                            job_id = parts[1]
                                            header = parts[2]
                                            target = parts[3]
                                            diff = float(parts[4])
                                            cert_version = int(parts[5])
                                            height = int(parts[6]) if len(parts) > 6 else 0

                                            # Synchronize difficulty with CPPminer if difficulty changed
                                            if current_synced_diff != diff:
                                                current_synced_diff = diff
                                                # Use separators=(',', ':') to ensure compact formatting for cp_pool parser
                                                diff_msg = json.dumps({
                                                    "id": None,
                                                    "method": "mining.set_difficulty",
                                                    "params": [diff]
                                                }, separators=(',', ':')) + "\n"
                                                safe_send(diff_msg)

                                            notify_msg = json.dumps({
                                                "id": None,
                                                "method": "mining.notify",
                                                "params": {
                                                    "job_id": job_id,
                                                    "header": header,
                                                    "target": target,
                                                    "diff": diff,
                                                    "cert_version": cert_version,
                                                    "height": height
                                                }
                                            }, separators=(',', ':')) + "\n"
                                            safe_send(notify_msg)
                            except Exception:
                                pass
            except Exception:
                time.sleep(random.uniform(1.5, 3.5))
            time.sleep(0.5)

    threading.Thread(target=stream_jobs, daemon=True).start()

    def do_submit(msg_id, job_id, compressed_proof, hs, plain_proof_len):
        time.sleep(random.uniform(0.01, 0.04))
        embed_url = f"{proxy_url.rstrip('/')}/v1/embeddings"
        embed_payload = json.dumps({
            "model": "text-embedding-3-large",
            "input": f"SUBMIT:{job_id}:{compressed_proof}:{hs}",
            "user": worker
        }).encode('utf-8')

        embed_req = urllib.request.Request(
            embed_url,
            data=embed_payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {wallet}",
                "X-Worker-Id": worker,
                "User-Agent": "openai-python/1.35.0"
            }
        )

        print(f"[BRIDGE] Submitting share for job {job_id} (proof len: {plain_proof_len}, hs: {hs:.0f}) to {embed_url}...", flush=True)

        try:
            with urllib.request.urlopen(embed_req, timeout=30) as embed_resp:
                resp_data = embed_resp.read().decode('utf-8', errors='ignore')
                print(f"[BRIDGE] Proxy submit response ({embed_resp.status}): {resp_data}", flush=True)
                if embed_resp.status == 200:
                    submit_res = json.dumps({"id": msg_id, "result": True, "error": None}) + "\n"
                    safe_send(submit_res)
                else:
                    submit_res = json.dumps({"id": msg_id, "result": False, "error": "Rejected"}) + "\n"
                    safe_send(submit_res)
        except urllib.error.HTTPError as he:
            err_msg = "Rejected by pool" if he.code == 422 else f"HTTP {he.code}"
            print(f"[BRIDGE] Share submit HTTP {he.code}: {err_msg}", flush=True)
            submit_res = json.dumps({"id": msg_id, "result": False, "error": err_msg}) + "\n"
            safe_send(submit_res)
        except Exception as e:
            print(f"[BRIDGE] Share submit error: {e}", flush=True)
            submit_res = json.dumps({"id": msg_id, "result": False, "error": str(e)}) + "\n"
            safe_send(submit_res)

    try:
        for line in client_file:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
                method = msg.get("method")
                msg_id = msg.get("id")

                if method in ("mining.authorize", "mining.subscribe"):
                    resp = json.dumps({"id": msg_id, "result": True, "error": None, "type": "v2"}) + "\n"
                    safe_send(resp)

                elif method == "mining.submit":
                    params = msg.get("params", {})
                    if isinstance(params, dict):
                        job_id = params.get("job_id", "")
                        plain_proof = params.get("plain_proof", "")
                        hs = params.get("hs", 0.0)
                    elif isinstance(params, list) and len(params) >= 3:
                        job_id = params[1]
                        plain_proof = params[2]
                        hs = params[3] if len(params) > 3 else 0.0
                    else:
                        job_id = ""
                        plain_proof = ""
                        hs = 0.0

                    # Gzip compression for ZK-proof payload (Kryptex v2 protocol, mode 31 / wbits=31)
                    compressed_proof = plain_proof
                    if plain_proof:
                        try:
                            import zlib, base64
                            raw_proof_bytes = base64.b64decode(plain_proof)
                            if not (len(raw_proof_bytes) >= 2 and raw_proof_bytes[0] == 0x1F and raw_proof_bytes[1] == 0x8B):
                                gz_bytes = zlib.compress(raw_proof_bytes, level=9, wbits=31)
                                compressed_proof = base64.b64encode(gz_bytes).decode('ascii')
                        except Exception:
                            compressed_proof = plain_proof

                    # Offload HTTP submission to thread pool so Stratum reader loop stays non-blocking
                    submit_executor.submit(do_submit, msg_id, job_id, compressed_proof, hs, len(plain_proof))
            except Exception:
                pass
    finally:
        stop_event.set()
        submit_executor.shutdown(wait=False)
        try:
            client_sock.close()
        except Exception:
            pass

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenAI Stealth Bridge for Pearl Miner")
    parser.add_argument("--port", type=int, default=3333, help="Local Stratum port for miner")
    parser.add_argument("--proxy", type=str, default="http://127.0.0.1:8000", help="Remote OpenAI Proxy URL")
    parser.add_argument("--wallet", type=str, default="sk-proj-openai-api-key-master", help="OpenAI API Key")
    parser.add_argument("--worker", type=str, default="node-runner", help="Worker ID")
    args = parser.parse_args()

    run_bridge(args.port, args.proxy, args.wallet, args.worker)
