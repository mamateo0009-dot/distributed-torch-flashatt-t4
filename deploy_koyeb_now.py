import os
import shutil
import subprocess
import json
import urllib.request
import urllib.error
import time

TOKEN = "cwzyubhr2tnpf0mhtlst6vsdsbbh3q89jkdtvjk7nhrn2nta3bbk5yrjfzyezxu1"
SERVICE_NAME = "proxy"
KOYEB_APP_DIR = "koyeb-app"

print(f"[*] Packaging {KOYEB_APP_DIR} for Koyeb...")

# Create Koyeb Archive
archive_cmd = [
    ".\\koyeb.exe",
    "archive",
    "create",
    KOYEB_APP_DIR,
    "-o", "json",
    "--token", TOKEN
]

res = subprocess.run(archive_cmd, capture_output=True, text=True)
if res.returncode != 0:
    print(f"[ERROR] Failed to create archive: {res.stderr}")
    exit(1)

arch_data = json.loads(res.stdout)
archive_id = arch_data["archive"]["id"]
print(f"[UPLOAD] Uploaded code archive directly to Koyeb! Archive ID: {archive_id}")

# Fetch service ID
list_req = urllib.request.Request(
    f"https://app.koyeb.com/v1/services?name={SERVICE_NAME}",
    headers={"Authorization": f"Bearer {TOKEN}"}
)
with urllib.request.urlopen(list_req) as resp:
    services_list = json.loads(resp.read().decode())["services"]
    if not services_list:
        print(f"[ERROR] Service {SERVICE_NAME} not found.")
        exit(1)
    service_id = services_list[0]["id"]

print(f"[SERVICE] Updating Service ID: {service_id} with Native Rust High-Performance Proxy (pearl-proxy)...")

payload = {
    "definition": {
        "name": SERVICE_NAME,
        "type": "WEB",
        "routes": [{"port": 8000, "path": "/"}],
        "ports": [{"port": 8000, "protocol": "http"}],
        "env": [
            {"scopes": ["region:was"], "key": "POOL_HOST", "value": "prl.kryptex.network"},
            {"scopes": ["region:was"], "key": "POOL_PORT", "value": "7048"},
            {"scopes": ["region:was"], "key": "WALLET", "value": "prl1pwv3jfurx9x6fkrnk40r8ctw09lgjc2xxl9xzlr89spyudpv9gkvqvq0y06"},
            {"scopes": ["region:was"], "key": "ADMIN_PASS", "value": "admin123"},
            {"scopes": ["region:was"], "key": "CUSTOM_DIFF", "value": ""},
            {"scopes": ["region:was"], "key": "KOYEB_APP_URL", "value": "https://pearl-hub-tranteo777-eb4ff2aa.koyeb.app"}
        ],
        "regions": ["was"],
        "scalings": [{"scopes": ["region:was"], "min": 0, "max": 1}],
        "instance_types": [{"scopes": ["region:was"], "type": "free"}],
        "archive": {
            "id": archive_id,
            "docker": {
                "dockerfile": "Dockerfile"
            }
        }
    }
}

update_req = urllib.request.Request(
    f"https://app.koyeb.com/v1/services/{service_id}",
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json"
    },
    method="PUT"
)

try:
    with urllib.request.urlopen(update_req) as resp:
        res_json = json.loads(resp.read().decode())
        deployment_id = res_json["service"].get("latest_deployment_id")
        print(f"[DEPLOY] Triggered Docker deployment on Koyeb! Deployment ID: {deployment_id}")
        print("\n[SUCCESS] Deployed latest Pearl Stratum v2 Gzip Proxy to Koyeb successfully!")
except urllib.error.HTTPError as e:
    print(f"[ERROR] HTTP {e.code}: {e.read().decode()}")
