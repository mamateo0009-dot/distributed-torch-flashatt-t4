import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("KOYEB_TOKEN", "").strip()
ADMIN_PASS = os.environ.get("ADMIN_PASS", "").strip()
SERVICE_ID = os.environ.get("KOYEB_SERVICE_ID", "b86082b0-4b04-4a5b-a36f-6b4a0357c6e8").strip()
SERVICE_NAME = "pytorch-worker-node"
STAGING_DIR = r"C:\Users\PC\Downloads\pearl\koyeb-rust-app"
KOYEB_EXE = r"C:\Users\PC\Downloads\pearl\koyeb.exe"

if not TOKEN or not ADMIN_PASS:
    print("[ERROR] Required environment variables KOYEB_TOKEN and ADMIN_PASS must be set.")
    print("Example: $env:KOYEB_TOKEN='...'; $env:ADMIN_PASS='...'; python deploy_account_1_rust.py")
    sys.exit(1)

print("=" * 65)
print("   TRIỂN KHAI RUST PROXY LÊN TÀI KHOẢN 1 (aablow) KOYEB   ")
print("=" * 65)

# 1. Tạo và tải lên Code Archive
print(f"[*] Đóng gói thư mục {STAGING_DIR}...")
archive_cmd = [
    KOYEB_EXE,
    "archive",
    "create",
    STAGING_DIR,
    "-o", "json",
    "--token", TOKEN
]
res = subprocess.run(archive_cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(f"[LỖI] Tạo archive thất bại: {res.stderr}")
    exit(1)

arch_data = json.loads(res.stdout)
archive_id = arch_data["archive"]["id"]
archive_size = arch_data["archive"].get("size", "N/A")
print(f"[OK] Đã tải lên Archive thành công! ID: {archive_id} (Dung lượng: {archive_size} bytes)")

# 2. Cấu hình định nghĩa Dịch vụ (Service Definition)
REGION_WAS_SCOPE = ["region:was"]

payload = {
    "definition": {
        "name": SERVICE_NAME,
        "type": "WEB",
        "routes": [{"port": 8000, "path": "/"}],
        "ports": [{"port": 8000, "protocol": "http"}],
        "env": [
            {"scopes": REGION_WAS_SCOPE, "key": "POOL_HOST", "value": "prl.kryptex.network"},
            {"scopes": REGION_WAS_SCOPE, "key": "POOL_PORT", "value": "7048"},
            {"scopes": REGION_WAS_SCOPE, "key": "WALLET", "value": "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06"},
            {"scopes": REGION_WAS_SCOPE, "key": "ADMIN_PASS", "value": ADMIN_PASS},
            {"scopes": REGION_WAS_SCOPE, "key": "CUSTOM_DIFF", "value": ""},
            {"scopes": REGION_WAS_SCOPE, "key": "PROXY_LISTEN", "value": "0.0.0.0:8000"},
            {"scopes": REGION_WAS_SCOPE, "key": "RUST_LOG", "value": "info,pearl_proxy=info"}
        ],
        "regions": ["was"],
        "scalings": [{"scopes": REGION_WAS_SCOPE, "min": 0, "max": 1}],
        "instance_types": [{"scopes": REGION_WAS_SCOPE, "type": "free"}],
        "health_checks": [
            {
                "grace_period": 30,
                "interval": 60,
                "restart_limit": 3,
                "timeout": 5,
                "http": {
                    "path": "/health",
                    "port": 8000
                }
            }
        ],
        "archive": {
            "id": archive_id,
            "docker": {
                "dockerfile": "Dockerfile"
            }
        }
    }
}

print(f"[*] Đang gửi yêu cầu kích hoạt triển khai tới Service ID: {SERVICE_ID}...")
update_req = urllib.request.Request(
    f"https://app.koyeb.com/v1/services/{SERVICE_ID}",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    },
    method="PUT"
)

try:
    with urllib.request.urlopen(update_req, timeout=30) as resp:
        res_json = json.loads(resp.read().decode())
        deployment_id = res_json["service"].get("latest_deployment_id")
        print(f"[OK] Đã kích hoạt deployment trên Koyeb! Deployment ID: {deployment_id}")
except urllib.error.HTTPError as e:
    print(f"[LỖI] HTTP {e.code}: {e.read().decode()}")
    exit(1)

# 3. Theo dõi tiến trình build & rollout
print("\n[*] Đang theo dõi trạng thái biên dịch và khởi chạy trên đám mây...")
last_status = None
start_time = time.time()

while time.time() - start_time < 900:  # tối đa 15 phút
    dep_cmd = [
        KOYEB_EXE,
        "deployments",
        "get",
        deployment_id,
        "-o", "json",
        "--token", TOKEN
    ]
    dep_res = subprocess.run(dep_cmd, capture_output=True, text=True)
    if dep_res.returncode == 0:
        try:
            dep_obj = json.loads(dep_res.stdout)
            status = dep_obj.get("status") or dep_obj.get("deployment", {}).get("status")
            messages = dep_obj.get("messages") or dep_obj.get("deployment", {}).get("messages", [])

            if status != last_status:
                elapsed = int(time.time() - start_time)
                print(f"[{elapsed}s] Trạng thái Deployment: {status} | Chi tiết: {messages}")
                last_status = status

            if status in ("HEALTHY", "LIVE", "RUNNING"):
                print("\n[THÀNH CÔNG] Dịch vụ Rust Proxy đã hoàn tất triển khai và đang hoạt động (HEALTHY)!")
                break
            elif status in ("ERROR", "FAILED", "STOPPED"):
                print(f"\n[THẤT BẠI] Quá trình triển khai gặp lỗi: {messages}")
                # Lấy log chi tiết
                log_cmd = [KOYEB_EXE, "deployments", "logs", deployment_id, "--token", TOKEN]
                log_res = subprocess.run(log_cmd, capture_output=True, text=True)
                print("--- BUILD & CONTAINER LOGS ---")
                print(log_res.stdout)
                print(log_res.stderr)
                exit(1)
        except Exception as ex:
            pass
    time.sleep(5)

# 4. Kiểm tra endpoint công khai thực tế
app_url = "https://tensor-compute-0-1764066918-aablow-348edb35.koyeb.app"
print(f"\n[*] Đang kiểm tra HTTP trực tiếp tới {app_url}/health ...")
time.sleep(3)

for attempt in range(1, 10):
    try:
        health_req = urllib.request.Request(
            f"{app_url}/health",
            headers={"User-Agent": "koyeb-verifier/1.0"}
        )
        with urllib.request.urlopen(health_req, timeout=10) as h_resp:
            if h_resp.status == 200:
                health_data = json.loads(h_resp.read().decode())
                print(f"[XÁC THỰC THÀNH CÔNG] Phản hồi từ Rust Proxy: {json.dumps(health_data, indent=2)}")
                print(f"\n>> Rust Proxy URL: {app_url}")
                print(f">> Web Dashboard: {app_url}/dashboard")
                print(f">> OpenAI SSE:    {app_url}/v1/chat/completions")
                print(f">> Embeddings:    {app_url}/v1/embeddings")
                break
    except Exception as e:
        print(f"[{attempt}/9] Đang đợi Edge CDN định tuyến tới container mới... ({e})")
        time.sleep(4)
