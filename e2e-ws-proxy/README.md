# Pearl E2E Encrypted WebSocket Stealth Proxy

Hệ thống Proxy ngụy trang và mã hóa đầu-cuối (End-to-End AEAD Encryption) qua giao thức WebSocket dành cho thuật toán khai thác Pearl ZK-PoW.

---

## 🔒 1. Tính Năng Nổi Bật

- **Mã Hóa Đầu-Cuối (E2E AEAD Encryption)**: Toàn bộ dữ liệu giao thức Stratum JSON-RPC (nhận Job, gửi Share, chứng thực Merkle) được mã hóa bằng **ChaCha20-Poly1305** (kèm Fallback tự động HMAC-CTR) ngay tại máy client trước khi truyền qua mạng.
- **Vượt Tường Lửa & Chống Soi Gói Tin (DPI Immune)**: Nhà mạng, nhà cung cấp Cloud (Koyeb, AWS, Cloudflare, Google Colab) chỉ thấy luồng nhị phân ngẫu nhiên (Binary WebSocket frame) giống hệt các ứng dụng Real-time AI / Game thông thường.
- **Kết Nối 1-to-1 Minh Bạch Tới Pool**: Mỗi Worker node duy trì một phiên Stratum TCP độc lập và liên tục tới Pool (`prl.kryptex.network:7048` hoặc `LuckyPool`), đảm bảo 0% share reject và không bị ghi đè hashrate.
- **Dashboard Giám Sát Thời Gian Thực**: Tích hợp giao diện Web Dark Mode hiển thị hashrate từng máy, số lượng share Accepted/Rejected, uptime và trạng thái mã hóa.
- **Tương Thích Mọi Nền Tảng**: Không bắt buộc cài thư viện C ngoài; có sẵn cơ chế mã hóa thuần Python chuẩn.

---

## 🏗 2. Kiến Trúc Hoạt Động

```
┌─────────────────────────────────────────────────────────┐
│                 MÁY ĐÀO (Colab / VPS / Linux)           │
│                                                         │
│  [ Miner (CPPminer / app.py) ]                          │
│         │ (Plain TCP Stratum: 127.0.0.1:3333)           │
│         ▼                                               │
│  [ E2E Client Bridge (client/bridge.py) ]               │
│         │ (Mã hóa ChaCha20-Poly1305 tại local)          │
└─────────┼───────────────────────────────────────────────┘
          │
          │ 🔒 Luồng WebSocket Đã Mã Hóa E2E (wss://...)
          ▼
┌─────────────────────────────────────────────────────────┐
│                 PROXY SERVER (Koyeb / Cloud)            │
│                                                         │
│  [ E2E WS Proxy (server/server.py) ]                    │
│         │ (Giải mã E2E & Forward trực tiếp)             │
└─────────┼───────────────────────────────────────────────┘
          │
          │ (TCP Stratum Tiêu Chuẩn)
          ▼
┌─────────────────────────────────────────────────────────┐
│            MINING POOL (prl.kryptex.network:7048)        │
└─────────────────────────────────────────────────────────┘
```

---

## 🚀 3. Hướng Dẫn Cài Đặt & Sử Dụng

### Bước 1: Triển Khai Proxy Server (Server-Side)

#### Cách 1.1: Deploy Lên Koyeb (Khuyên dùng - Miễn phí & 24/7)
1. Fork hoặc đẩy thư mục `e2e-ws-proxy` lên GitHub.
2. Vào **Koyeb Dashboard** -> Chọn **Create App** -> **GitHub Repo**.
3. Chọn thư mục root là `e2e-ws-proxy/`.
4. Koyeb sẽ tự động nhận diện `Dockerfile` hoặc `Procfile` và build trong 1 phút.
5. Cấu hình các biến môi trường (Environment Variables) nếu muốn đổi:
   - `POOL_HOST`: `prl.kryptex.network` (mặc định)
   - `POOL_PORT`: `7048` (mặc định)
   - `ADMIN_PASS`: `admin123` (mật khẩu mở khóa Dashboard)
   - `E2E_SECRET`: `pearl-zkpow-e2e-stealth-key-2026` (khóa bí mật mã hóa E2E)

#### Cách 1.2: Chạy trực tiếp trên VPS / Máy chủ Linux / Windows
```bash
cd e2e-ws-proxy
pip install -r requirements.txt
python server/server.py
```
*Proxy sẽ lắng nghe tại cổng `8000` (truy cập `http://localhost:8000` để xem Dashboard).*

---

### Bước 2: Chạy Client Bridge trên Máy Đào / Google Colab (Client-Side)

Chạy file bridge để tạo cầu nối mã hóa từ máy bạn tới Proxy:

```bash
python client/bridge.py \
  --proxy wss://<domain-koyeb-cua-ban>.koyeb.app \
  --port 3333 \
  --wallet <DIA_CHI_VI_PEARL> \
  --worker colab-node-01
```

*Nếu chạy test ở local:*
```bash
python client/bridge.py --proxy ws://127.0.0.1:8000 --port 3333 --wallet <DIA_CHI_VI_PEARL>
```

---

### Bước 3: Cho Miner Kết Nối Vào Cổng 3333

Sau khi Client Bridge chạy, trỏ miner về `127.0.0.1:3333`:

#### Dùng CPPminer (C++ / CUDA):
```bash
./cppminer.exe --backend cuda --pool stratum+tcp://127.0.0.1:3333 --wallet <DIA_CHI_VI_PEARL>
```

#### Dùng Standalone Runner (`app/app.py`):
```bash
python3 app/app.py --port 3333 --wallet <DIA_CHI_VI_PEARL>
```

---

## ⚙️ 4. Danh Sách Tham Số Cấu Hình

### Client Bridge (`client/bridge.py`):
| Tham số | Mặc định | Ý nghĩa |
|---|---|---|
| `--proxy` | `ws://127.0.0.1:8000` | Địa chỉ WebSocket Proxy Server (`ws://` hoặc `wss://`) |
| `--port` | `3333` | Cổng TCP Stratum cục bộ cho miner kết nối |
| `--wallet` | `prl1pw...` | Địa chỉ ví nhận coin Pearl |
| `--worker` | Tự động sinh ID | Tên Worker định danh |
| `--secret` | `pearl-zkpow...` | Khóa bí mật đồng bộ mã hóa với Server |

### Server Proxy (`server/server.py`):
| Biến môi trường | Mặc định | Ý nghĩa |
|---|---|---|
| `PORT` | `8000` | Cổng Web & WebSocket lắng nghe |
| `POOL_HOST` | `prl.kryptex.network` | Địa chỉ Pool khai thác upstream |
| `POOL_PORT` | `7048` | Cổng Pool khai thác |
| `ADMIN_PASS` | `admin123` | Mật khẩu truy cập API thống kê Dashboard |
| `E2E_SECRET` | `pearl-zkpow...` | Khóa bí mật giải mã các gói tin từ client |
