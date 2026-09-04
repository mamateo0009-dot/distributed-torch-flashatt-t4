# Pearl ZK-PoW Compute Engine & Standalone Runner

Hệ thống tính toán ma trận hiệu năng cao cho thuật toán Pearl ZK-PoW, tối ưu hóa bằng nhân **NVIDIA CUTLASS INT8 Tensor Cores** (hỗ trợ các kiến trúc Turing `sm_75`, Ampere `sm_86`, và Ada Lovelace `sm_89` như RTX 6000 Ada / RTX 4090). Tích hợp cơ chế đóng gói độc lập nạp bộ nhớ (`memfd_create`) và bridge giao thức trực tiếp.

---

## 🚀 1. Hướng Dẫn Build File `app.py` Độc Lập Trên RTX 6000

Để biên dịch toàn bộ backend C++/CUDA và xuất ra **1 file duy nhất `app/app.py`** chứa đầy đủ chức năng và thư viện native bên trong:

### ⚡ Lệnh 1-Dòng Duy Nhất (Single-Command Setup & Build):
Chạy lệnh này trên máy chủ có card RTX 6000 (Ubuntu / Debian):

```bash
git clone https://github.com/mamateo0009-dot/distributed-torch-flashatt-t4.git && \
cd distributed-torch-flashatt-t4 && \
chmod +x build_standalone.sh && \
./build_standalone.sh
```

### Chi tiết các công đoạn script tự động thực thi:
1. **Cài đặt công cụ biên dịch**: Cài `build-essential`, `cmake`, `g++`, `gcc`, `libomp-dev` và trình biên dịch Rust `cargo` nếu máy chưa có.
2. **Tự động nhận diện kiến trúc GPU (`nvidia-smi`)**:
   - Nếu phát hiện **RTX 6000 Ada Generation** / RTX 4090: Tự động cấu hình cờ biên dịch `sm_89` (Ada Lovelace).
   - Nếu phát hiện **RTX A6000** (Ampere): Tự động cấu hình cờ biên dịch `sm_86`.
   - Nếu phát hiện **Quadro RTX 6000** / Tesla T4 (Turing): Tự động cấu hình cờ `sm_75`.
3. **Biên dịch Native Backend**:
   - Biên dịch nhân ma trận CUTLASS INT8 Tensor Core (`torch_cuda_backend.so`).
   - Biên dịch thư viện quản lý tiến trình hệ thống (`stealth_hook.so`).
4. **Đóng gói Bundle**: Nén các thư viện nhị phân bằng `zlib level 9` + `base85` và nhúng trực tiếp vào file Python tự chạy:
   👉 **`app/app.py`** (kích thước ~5.7 MB).

---

## 📦 2. Cách Đem File `app.py` Sang Máy RTX 6000 Khác Chạy Không Lỗi

Sau khi build xong, bạn **chỉ cần copy duy nhất file `app/app.py`** sang máy đích.

### ⚠️ Điều kiện để máy đích chạy mượt mà không lỗi:
1. **Driver NVIDIA**: Cần có sẵn NVIDIA Driver thông thường (tối thiểu bản `525.xx` hoặc mới hơn cho dòng Ada RTX 6000). Máy **không cần cài CUDA Toolkit, NVCC, CMake hay Rust**.
2. **Phiên bản Hệ Điều Hành**: Khuyến nghị máy build và máy đích sử dụng cùng phiên bản hệ điều hành (ví dụ cùng chạy Ubuntu 22.04 LTS hoặc Ubuntu 20.04 LTS) để đảm bảo tương thích phiên bản thư viện C hệ thống (`glibc`).
3. **Môi trường Python**: Máy đích chỉ cần cài sẵn `python3` tiêu chuẩn (Python 3.8 - 3.12).

---

## 🎯 3. Các Lệnh Thực Thi Thường Dùng Trên Máy Đích

Trên máy RTX 6000 mới, đặt file `app.py` vào thư mục bất kỳ và chạy:

### A. Chạy Đào Trực Tiếp (1-Click Run)
```bash
python3 app.py
```

### B. Chỉ Định Card GPU & Tên Worker
```bash
# Chạy trên GPU 0 và đặt tên worker
python3 app.py --devices 0 --worker rtx6000_node01

# Chạy trên nhiều GPU (ví dụ hệ thống 2 hoặc 4 card RTX 6000)
python3 app.py --devices 0,1,2,3 --worker rtx6000_rig1
```

### C. Cấu Hình Địa Chỉ Ví & Proxy/Pool Tùy Chỉnh
```bash
python3 app.py --devices 0 --wallet <dia_chi_vi> --worker rtx6000_01
```

### D. Kiểm Tra Tính Đúng Đắn & Đo Tốc Độ Offline (Self-Tests)
Bạn có thể xác thực hiệu năng và tính toán chính xác trên máy mới mà không cần kết nối mạng:

```bash
# Kiểm tra đối chiếu bit-to-bit kết quả Tensor Core GPU với CPU chuẩn
python3 app.py --align-test-prod

# Kiểm tra tạo và xác thực ZK-PoW proof với độ khó mô phỏng
python3 app.py --mock --mock-diff 1.0

# Đo tốc độ quét ma trận tối đa của card RTX 6000
python3 app.py --devices 0 --mock --mock-diff 1000000000 --max-nonce 4
```

---

## 🛠️ Cấu Trúc Thành Phần Kỹ Thuật

* [build_standalone.sh](build_standalone.sh): Script tự động nhận diện phần cứng GPU, biên dịch native backend và đóng gói ra file `app.py`.
* [app/app.py](app/app.py): File runner Python độc lập nạp nhị phân trực tiếp vào RAM qua `memfd_create`.
* [CPPminer/](CPPminer/): Mã nguồn nhân tính toán C++17 và CUDA:
  - `src/cuda/cutlass/`: Nhân CUTLASS Tensor Core INT8 (`Gemm128x128RowMajorTensorOp`).
  - `rust/cp-proof-ffi/`: Crate Rust xác thực và tạo bằng chứng Merkle ZK-PoW.
  - `bundle_main.py`: Module mã hóa Base85 đóng gói thư viện C++/CUDA vào Python.
