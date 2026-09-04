#!/usr/bin/env bash
# ==============================================================================
# 1-Command Autonomous Builder & Packager for Pearl Standalone Miner (app.py)
# ==============================================================================
# This script:
# 1. Installs all required Linux system build tools, CMake, and Rust compiler.
# 2. Auto-detects NVIDIA GPU compute architecture (sm_75, sm_80, sm_89, sm_90, etc.).
# 3. Compiles high-performance CUDA CUTLASS backend & procfs/NVML stealth hook.
# 4. Bundles everything into a single zero-dependency standalone file: app/app.py.
# 5. Resulting app/app.py can be copied to ANY Linux machine with NVIDIA GPU and run!
# ==============================================================================

set -e

echo "===================================================================="
echo "  PEARL AUTONOMOUS 1-COMMAND MINER BUILDER & PACKAGER"
echo "===================================================================="

# 1. System packages
echo "[*] Step 1/4: Checking system dependencies..."
if command -v apt-get &> /dev/null; then
    apt-get update -qq || true
    apt-get install -y -qq build-essential cmake g++ gcc git curl pkg-config libssl-dev libomp-dev || true
elif command -v dnf &> /dev/null; then
    dnf install -y cmake gcc-c++ gcc git curl openssl-devel libgomp || true
elif command -v pacman &> /dev/null; then
    pacman -Sy --noconfirm base-devel cmake git curl openssl || true
fi

# 2. Rust toolchain
if ! command -v cargo &> /dev/null; then
    echo "[*] Installing Rust toolchain..."
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
fi
source "$HOME/.cargo/env" 2>/dev/null || export PATH="$HOME/.cargo/bin:$PATH"
echo "[+] Cargo version: $(cargo --version 2>/dev/null || echo 'cargo detected')"

# 3. Detect GPU Architecture
CUDA_ARCH=75
if command -v nvidia-smi &> /dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -n 1)
    echo "[*] Detected GPU: $GPU_NAME"
    if echo "$GPU_NAME" | grep -qE "6000|Ada|4090|4080|L40"; then
        CUDA_ARCH=89
    elif echo "$GPU_NAME" | grep -qE "A100|H100"; then
        CUDA_ARCH=80
    elif echo "$GPU_NAME" | grep -qE "V100"; then
        CUDA_ARCH=70
    elif echo "$GPU_NAME" | grep -qE "T4"; then
        CUDA_ARCH=75
    fi
fi
echo "[+] Selected CUDA Compute Architecture: sm_${CUDA_ARCH}"

# 4. Build CUDA Backend & Stealth Hook
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CPPMINER_DIR="${SCRIPT_DIR}/CPPminer"

echo "[*] Step 2/4: Compiling native CUDA engine and stealth library..."
cd "${CPPMINER_DIR}"
chmod +x build.sh
./build.sh --backend cuda --cuda-arch "${CUDA_ARCH}"

# 5. Packaging into Standalone app.py
echo "[*] Step 3/4: Packing standalone self-contained app/app.py..."
python3 bundle_main.py

# 6. Verification
echo "[*] Step 4/4: Verifying generated standalone app.py..."
STANDALONE_FILE="${SCRIPT_DIR}/app/app.py"
if [ -f "${STANDALONE_FILE}" ] && [ -s "${STANDALONE_FILE}" ]; then
    FILE_SIZE=$(ls -lh "${STANDALONE_FILE}" | awk '{print $5}')
    echo "===================================================================="
    echo " [SUCCESS] Standalone file generated successfully!"
    echo " File: ${STANDALONE_FILE} (${FILE_SIZE})"
    echo ""
    echo " How to use on other machines:"
    echo "   1. Copy only 'app/app.py' to any Linux GPU machine (VPS, Colab, RunPod)."
    echo "   2. Run directly with zero build dependencies:"
    echo "        python3 app.py --devices 0"
    echo "===================================================================="
else
    echo "[ERROR] Failed to generate ${STANDALONE_FILE}"
    exit 1
fi
