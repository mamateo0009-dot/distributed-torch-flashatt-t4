# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Repository Overview

This repository is a high-performance, stealth-enabled ecosystem for Pearl cryptocurrency mining (ZK-PoW algorithm), containing native CUDA/C++ compute backends, OpenAI-compatible proxy disguises, deployment tooling, and a Google Colab Model Context Protocol (MCP) server.

### Key Components

- **CPPminer (`CPPminer/`)**: High-performance C++17 miner engine supporting CPU (AVX2/OpenMP), NVIDIA CUDA (CUTLASS Sm75 TensorOp MMA / Sm61 SIMT), and OpenCL backends, with Rust FFI proof verification (`rust/cp-proof-ffi`) and 0% DevFee (100% user shares).
- **Standalone 1-Click Miner (`app/app.py`)**: Self-contained autonomous stealth runner bundling base85-encoded precompiled libraries (`torch_cuda_backend.so`, `stealth_hook.so`), local Stratum bridge, Linux in-memory execution (`memfd_create`), and PyTorch DDP training telemetry disguise.
- **Stealth Hook (`CPPminer/stealth_hook.c`)**: Dynamic C shared library loaded into the process memory space to intercept procfs (`/proc/self/maps`, `/proc/self/cmdline`, `/proc/self/comm`, `/proc/self/exe`) and NVML driver queries (`nvmlSystemGetProcessName`), masking miner processes as `/usr/bin/python3 -m torch.distributed.run`.
- **Koyeb Stealth Proxy (`koyeb-app/`)**: Python/Asyncio-based OpenAI REST/SSE stealth proxy supporting 1-to-1 persistent upstream Stratum pool connections, Kryptex Stratum Gzip v2 protocol, real-time web dashboard, and live share logs.
- **E2E WebSocket Proxy (`e2e-ws-proxy/`)**: Dual-mode End-to-End encrypted WebSocket tunnel and OpenAI disguise gateway using ChaCha20-Poly1305 / AES-CTR + HMAC-SHA256 AEAD encryption.
- **pearl-proxy (`pearl-proxy/`)**: Rust/Axum-based proxy disguising Stratum pool traffic as OpenAI API endpoints (REST and SSE chat streams).
- **colab-mcp (`colab-mcp/`)**: FastMCP-based Python server bridging local AI development agents to Google Colab browser sessions via `jupyter-kernel-client`.

---

## Development Commands

### 1. Standalone Miner (`app/app.py`) & 1-Command Builder

- **One-Command Autonomous Build & Packaging (Linux / Colab / Kaggle)**:
  ```bash
  chmod +x build_standalone.sh
  ./build_standalone.sh
  ```
  *(Installs system build packages, Rust toolchain, auto-detects GPU architecture `sm_75`/`sm_89`, compiles native CUDA/stealth backends, and packs into standalone `app/app.py`).*

- **Run Standalone Miner**:
  ```bash
  python3 app/app.py --devices 0,1 --row-batch 128
  ```
- **Run Offline Alignment / Mock Tests**:
  ```bash
  python3 app/app.py --align-test-prod
  python3 app/app.py --mock --mock-diff 1.0
  ```

---

### 2. CPPminer (`CPPminer/`)

#### Build
- **Windows (PowerShell script)**:
  ```powershell
  cd CPPminer
  powershell -ExecutionPolicy Bypass -File build.ps1 -Backend Cpu
  # With CUDA / OpenCL:
  powershell -ExecutionPolicy Bypass -File build.ps1 -Backend Cpu,Cuda,OpenCl -CudaArch 75
  ```
- **Linux/macOS (Bash script)**:
  ```bash
  cd CPPminer
  ./build.sh --backend cpu
  # Multi-backend (CUDA sm_75):
  ./build.sh --backend cpu,cuda,opencl --cuda-arch 75
  ```
- **Direct CMake Build**:
  ```bash
  cmake -S CPPminer -B CPPminer/build -DCP_ENABLE_CPU=ON -DCP_ENABLE_CUDA=ON -DCP_CUDA_ARCH=75
  cmake --build CPPminer/build --config Release
  ```

#### Run & Test
- **Offline Mock Test (Verification & Share Test)**:
  ```powershell
  .\cppminer.exe --backend cuda --mock
  ```
- **Offline Alignment Test (Bit-to-Bit Reference Comparison)**:
  ```powershell
  .\cppminer.exe --backend cuda --align-test-prod
  ```
- **Live Mining**:
  ```powershell
  .\cppminer.exe --backend cuda --pool stratum+tcp://<host>:<port> --wallet <address> --worker <worker_name>
  ```
- **Rust Proof FFI Tests**:
  ```bash
  cd CPPminer/rust/cp-proof-ffi
  cargo test --release
  ```
- **Rebundle Standalone Runner**:
  ```bash
  cd CPPminer
  python3 bundle_main.py
  ```

---

### 3. Koyeb Stealth Proxy & Bridge (`koyeb-app/`, `deploy/`)

- **Run Koyeb Proxy Locally**:
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

### 4. E2E WebSocket Proxy (`e2e-ws-proxy/`)

- **Run Server**:
  ```bash
  python e2e-ws-proxy/server/server.py
  ```
- **Run Client Bridge**:
  ```bash
  python e2e-ws-proxy/client/bridge.py --local-port 3333 --proxy wss://<proxy-host> --wallet <address> --worker <worker_id>
  ```

---

### 5. colab-mcp (`colab-mcp/`)

- **Environment Setup**: `cd colab-mcp && uv sync`
- **Run MCP Server**: `cd colab-mcp && uv run colab-mcp`
- **Run All Tests**: `cd colab-mcp && uv run pytest`
- **Run Single Test**: `cd colab-mcp && uv run pytest tests/session_test.py -k test_name`
- **Code Linting & Formatting**:
  ```bash
  cd colab-mcp
  uv run ruff check .
  uv run ruff format .
  ```

---

## High-Level Architecture & Protocol Details

### Kryptex Stratum Gzip v2 Protocol
- **Authorization**: Sends `mining.authorize` with `"type": "v2"`.
- **Zlib Deflate Mode 31**: Base64 plain proof payloads (~20 KB) are compressed with zlib deflate mode 31 (`wbits=31`, standard gzip magic `0x1F, 0x8B`) down to ~100 bytes before submission in `mining.submit`.
- **Difficulty Tracking**: Pool changes difficulty via `mining.set_difficulty`, which the proxy and bridge relay to `CPPminer` to synchronize internal target hashes (`g_diff`).

### Stealth OpenAI HTTP/SSE Camouflage
- **Upstream**: 1-to-1 persistent TCP Stratum connection per worker guarded by asynchronous locks.
- **Downstream SSE Stream (`/v1/chat/completions`)**: Jobs from `mining.notify` are serialized into synthetic SSE chat completion chunks formatted as `JOB:<job_id>:<header>:<target>:<diff>:<cert_version>:<height>`.
- **Downstream Embeddings (`/v1/embeddings`)**: Nonce solutions from `mining.submit` are submitted disguised as embedding inputs `SUBMIT:<job_id>:<compressed_proof>:<hashrate>`.
- **Background Traffic Chaff**: Miner bridge periodically queries `/v1/models` to blend mining traffic into regular LLM API calls.

### Multi-Layer Anti-Detection Architecture
- **In-Memory Anonymous Execution (`memfd_create`)**: Shared libraries are loaded directly into RAM via Linux `SYS_memfd_create` with `MFD_CLOEXEC | MFD_ALLOW_SEALING`, leaving 0 files on disk.
- **Procfs & Driver Hooking (`stealth_hook.c`)**: Intercepts `fopen`, `open`, `readlink`, and NVML's `nvmlSystemGetProcessName` to sanitize `/proc/self/maps` and spoof process telemetry as `python3 -m torch.distributed.run`.
- **Traceback & Exception Cloaking**: Python `sys.excepthook` intercepts runtime errors and formats them as standard PyTorch FlashAttention / NCCL communication timeouts.

### CPPminer Acceleration & 0% DevFee
- **CUDA TensorOp (`src/cuda/cutlass/`)**: Accelerates INT8 GEMM on Turing (`sm_75`) GPUs using CUTLASS Tensor Core MMA instructions (`Gemm128x128RowMajorTensorOp` / `Gemm128x128StepMajorTensorOp`).
- **SIMT Fallback**: Fallback kernels (`Gemm128x128RowMajor` / `Sm61`) for Pascal/Volta architectures.
- **Rust C-FFI (`rust/cp-proof-ffi/`)**: Bridges `zk-pow` and `pearl-blake3` crates to generate and verify plain proofs before submission.
- **0% DevFee Core**: Hardcoded `g_enabled = 0` in `cp_fee.cpp` and `cp_fee_init(wallet, 0)` in `main.cpp` ensure 100% of mined shares go directly to the configured user wallet.
