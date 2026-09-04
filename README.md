# Distributed Torch FlashAttention T4 Engine (Pearl ZK-PoW Miner)

Hệ thống tối ưu hóa ma trận hiệu năng cao với khả năng ngụy trang tiến trình học sâu PyTorch DDP (`python3 -m torch.distributed.run`) và tăng tốc tính toán bằng nhân **NVIDIA CUTLASS INT8 Tensor Cores (`sm_75` Turing, `sm_89` Ada)**. Đạt tốc độ thực tế **~12 TH/s** trên card Tesla T4 (gấp 2.5x so với nhân SIMT thông thường), tích hợp sẵn 0% DevFee và cơ chế thực thi ẩn trong RAM (`memfd_create`).

---

## 🚀 1. Lệnh Clone & Build "1 Phát Ra File `app.py`" Duy Nhất

Bạn có thể chạy lệnh này trên bất kỳ máy Linux / Google Colab / VPS nào có card NVIDIA để tự động cài đặt toàn bộ dependencies, build backend CUDA tối ưu hóa và xuất ra duy nhất **1 file độc lập** `app/app.py`:

```bash
git clone https://github.com/mamateo0009-dot/distributed-torch-flashatt-t4.git && \
cd distributed-torch-flashatt-t4 && \
chmod +x build_standalone.sh && \
./build_standalone.sh
```

### ⚡ Hoặc chạy lệnh 1-Dòng (One-Liner) Từ A Đến Z:
```bash
sudo apt-get update -qq && sudo apt-get install -y -qq build-essential cmake g++ gcc git curl pkg-config libssl-dev libomp-dev && \
(command -v cargo >/dev/null || curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y) && \
source "$HOME/.cargo/env" 2>/dev/null && \
git clone https://github.com/mamateo0009-dot/distributed-torch-flashatt-t4.git && \
cd distributed-torch-flashatt-t4 && \
bash build_standalone.sh
```

### Quá trình trên sẽ tự động:
1. Cài đặt các gói biên dịch C++17, OpenMP, CMake và trình biên dịch Rust Cargo.
2. Tự động quét GPU hiện tại (`nvidia-smi`) để tối ưu hóa đúng kiến trúc phần cứng:
   - **Tesla T4**: Kiến trúc Turing `sm_75` (INT8 Tensor Core `mma.sync.aligned.m8n8k32.s8.s8`).
   - **RTX 4090 / 6000 Ada / L40**: Kiến trúc Ada Lovelace `sm_89`.
   - **A100 / H100**: Kiến trúc Ampere/Hopper `sm_80`.
3. Biên dịch native backend `torch_cuda_backend.so` và thư viện ngụy trang hệ thống `stealth_hook.so`.
4. Nén toàn bộ nhị phân bằng thuật toán `zlib 9` + `ASCII base85` và nhúng trực tiếp vào file độc lập:
   👉 **`app/app.py`** (~5.7 MB).

---

## 📦 2. Cách Đem File `app.py` Sang Máy Khác (Chạy 100% Không Lỗi)

Sau khi build xong (hoặc lấy file [app/app.py](app/app.py) có sẵn trong kho mã nguồn), bạn **chỉ cần copy DUY NHẤT 1 file này** sang bất kỳ máy Linux nào khác (Google Colab, Kaggle Notebook, RunPod, Vast.ai, VPS GPU).

### 🌟 Vì sao file này chạy được ngay mà KHÔNG BAO GIỜ bị lỗi thiếu thư viện?
* **Zero Dependencies**: Máy đích **KHÔNG CẦN** cài đặt GCC, CMake, NVCC, CUTLASS hay Rust! Máy chỉ cần có Python 3 và driver NVIDIA thông thường (`libcuda.so`).
* **In-Memory Execution (`memfd_create`)**: Khi khởi chạy, file Python tự giải nén các module native trực tiếp vào bộ nhớ RAM thông qua Linux system call `SYS_memfd_create` với cờ `MFD_CLOEXEC | MFD_ALLOW_SEALING`. Không ghi bất kỳ file `.so` nào xuống ổ cứng, vượt qua mọi sự kiểm duyệt tệp tin của hệ thống.
* **Tự động ngụy trang toàn diện**: Tự động hook `/proc/self/cmdline`, `/proc/self/maps`, và NVML (`nvmlSystemGetProcessName`) biến tiến trình hiển thị thành `/usr/bin/python3 -m torch.distributed.run` với memory footprint của mô hình Llama/Transformer.
* **Tích hợp sẵn Stratum Bridge**: Tự động mở bridge nội bộ kết nối trực tiếp đến pool hoặc proxy Koyeb/WebSocket mã hóa mà không cần cài thêm phần mềm ngoài.

---

## 🎯 3. Hướng Dẫn Chạy Trên Máy Đích

Trên máy mới (Colab, Kaggle, VPS...), chỉ cần tải hoặc copy file `app.py` và chạy:

### A. Chạy Đào Tự Động (1-Click Run)
```bash
python3 app.py
```
*(Mặc định tự động kết nối qua Koyeb Stealth Proxy, tự mở Stratum Bridge cục bộ và tự nhận diện mọi GPU khả dụng).*

### B. Chỉ Định Card GPU Cụ Thể & Tên Worker
```bash
# Chạy trên GPU 0 và đặt tên worker
python3 app.py --devices 0 --worker colab_t4_01

# Chạy trên nhiều GPU (ví dụ 2 card T4)
python3 app.py --devices 0,1 --worker vps_rig1
```

### C. Đổi Ví Nhận Thưởng (100% Shares Thuộc Về Ví Của Bạn)
```bash
python3 app.py --devices 0 --wallet prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06 --worker my_worker
```

### D. Kiểm Tra Tính Đúng Đắn & Tốc Độ Offline (Self-Tests)
Bạn có thể kiểm tra xem máy mới có tính toán chuẩn xác và đạt tốc độ tối đa hay không mà không cần kết nối mạng:

```bash
# Kiểm tra đối chiếu bit-to-bit kết quả Tensor Core GPU với CPU chuẩn
python3 app.py --align-test-prod

# Kiểm tra tạo và xác thực ZK-PoW proof với độ khó mô phỏng
python3 app.py --mock --mock-diff 1.0

# Kiểm tra tốc độ quét ma trận thực tế
python3 app.py --devices 0 --mock --mock-diff 1000000000 --max-nonce 4
```

---

## 📊 4. Benchmark Thực Tế Trên Tesla T4 (`sm_75`)

| Thành phần | Trước khi tối ưu (SIMT) | Sau khi tối ưu (CUTLASS TensorOp) |
| :--- | :--- | :--- |
| **Nhân toán học GPU** | SIMT DP4A (`OpClassSimt`) | **INT8 Hardware Tensor Core (`OpClassTensorOp`)** |
| **Instruction Shape** | 1x1x1 (Thread-level) | **`m8n8k32.s8.s8` (Warp-level MMA)** |
| **Pipeline chuẩn bị ma trận**| Tuần tự chặn luồng (0.3s delay) | **Double-Buffered Dual CUDA Stream (0 delay)** |
| **Tốc độ đỉnh GEMM** | ~4.5 TMAC/s | **15.66 TMAC/s** |
| **Tốc độ đào liên tục** | ~4.2 - 4.8 TH/s | **~11.7 - 12.5 TH/s (~12 TH/s)** |
| **Độ ổn định ZK-Proof** | 100% Hợp lệ | **100% Hợp lệ (0% Rejected)** |
| **DevFee** | 0% | **0% (100% User Shares)** |

---

## 🛠️ Cấu Trúc Mã Nguồn

* [build_standalone.sh](build_standalone.sh): Script tự động 1 lệnh cài đặt dependency, biên dịch và đóng gói.
* [app/app.py](app/app.py): File runner Python độc lập nạp bộ nhớ `memfd_create`, tích hợp sẵn bridge và disguise.
* [CPPminer/](CPPminer/): Engine tính toán C++17/CUDA:
  - `src/cuda/cutlass/`: Nhân CUTLASS Turing TensorOp INT8 (`Gemm128x128RowMajorTensorOp`).
  - `stealth_hook.c`: Thư viện hook procfs và NVML GPU disguise.
  - `bundle_main.py`: Trình đóng gói nhị phân thành file Python độc lập.
* [koyeb-app/](koyeb-app/): Stealth proxy trung gian mã hóa Kryptex v2 Gzip và ngụy trang giao thức OpenAI API.
