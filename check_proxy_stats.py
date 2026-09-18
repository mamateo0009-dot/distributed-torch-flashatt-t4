import urllib.request
import json
from datetime import datetime

url = "https://pearl-hub-tranteo777-eb4ff2aa.koyeb.app/admin/stats?pass=admin123"
try:
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read().decode('utf-8'))
    print("==================================================")
    print("           THỐNG KÊ PROXY REALTIME               ")
    print("==================================================")
    print(f"• Uptime:               {data.get('uptime_seconds')} giây")
    print(f"• Active Workers:       {data.get('active_workers')} máy")
    print(f"• Tổng Hashrate:        {data.get('total_hashrate', 0) / 1e12:.2f} TH/s")
    print(f"• Shares Accepted:      {data.get('total_shares_accepted')}")
    print(f"• Shares Rejected:      {data.get('total_shares_rejected')}")
    print(f"• Block Height hiện tại:{data.get('current_block_height')}")
    print("==================================================")
    print("10 SHARES MỚI NHẤT GỬI LÊN POOL:")
    shares = data.get('recent_shares', [])
    if not shares:
        print("  (Chưa có share nào sau khi deploy lại proxy)")
    for idx, s in enumerate(shares[:10], 1):
        ts = datetime.fromtimestamp(s.get('timestamp', 0)).strftime('%H:%M:%S')
        stt = "✅ ACCEPTED" if s.get('accepted') else "❌ REJECTED"
        print(f"  {idx}. [{ts}] Worker: {s.get('worker_id')} | Job: {s.get('job_id')} | {stt} | Hash: {s.get('hashrate',0)/1e12:.2f} TH/s")
except Exception as e:
    print(f"Lỗi: {e}")
