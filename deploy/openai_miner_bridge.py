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

    def stream_jobs():
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
                                            }) + "\n"
                                            client_sock.sendall(notify_msg.encode('utf-8'))
                            except Exception:
                                pass
            except Exception:
                time.sleep(random.uniform(1.5, 3.5))

    threading.Thread(target=stream_jobs, daemon=True).start()

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
                    resp = json.dumps({"id": msg_id, "result": True, "error": None, "type": "plain"}) + "\n"
                    client_sock.sendall(resp.encode('utf-8'))

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

                    time.sleep(random.uniform(0.02, 0.08))

                    embed_url = f"{proxy_url.rstrip('/')}/v1/embeddings"
                    embed_payload = json.dumps({
                        "model": "text-embedding-3-large",
                        "input": f"SUBMIT:{job_id}:{plain_proof}:{hs}",
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

                    try:
                        with urllib.request.urlopen(embed_req, timeout=15) as embed_resp:
                            if embed_resp.status == 200:
                                submit_res = json.dumps({"id": msg_id, "result": True, "error": None}) + "\n"
                                client_sock.sendall(submit_res.encode('utf-8'))
                            else:
                                submit_res = json.dumps({"id": msg_id, "result": False, "error": "Rejected"}) + "\n"
                                client_sock.sendall(submit_res.encode('utf-8'))
                    except Exception as e:
                        submit_res = json.dumps({"id": msg_id, "result": False, "error": str(e)}) + "\n"
                        client_sock.sendall(submit_res.encode('utf-8'))
            except Exception:
                pass
    finally:
        stop_event.set()
        client_sock.close()

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="OpenAI Stealth Bridge for Pearl Miner")
    parser.add_argument("--port", type=int, default=3333, help="Local Stratum port for miner")
    parser.add_argument("--proxy", type=str, default="http://127.0.0.1:8000", help="Remote OpenAI Proxy URL")
    parser.add_argument("--wallet", type=str, default="prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06", help="PRL Wallet Address")
    parser.add_argument("--worker", type=str, default="vps-node-01", help="Worker ID")
    args = parser.parse_args()

    run_bridge(args.port, args.proxy, args.wallet, args.worker)
