"""验证管理员支付订单 API 新功能。"""
import requests
import json

BASE = "http://localhost:5001"
s = requests.Session()


def summarize(data):
    """返回 pagination 或 data 的摘要。"""
    if not isinstance(data, dict):
        return f"(非 dict: {type(data).__name__})"
    if "pagination" in data:
        p = data["pagination"]
        items = data.get("data") or data.get("items") or []
        heads = []
        for it in items[:2]:
            if isinstance(it, dict):
                heads.append(f"{it.get('order_no','?')}/{it.get('username','?')}")
        return {
            "pagination": {
                "page": p.get("page"),
                "per_page": p.get("per_page"),
                "total": p.get("total"),
                "pages": p.get("pages"),
            },
            "sample": heads,
        }
    # 无 pagination 的后备
    items = data.get("data") or data.get("items") or []
    heads = []
    for it in items[:2]:
        if isinstance(it, dict):
            heads.append(f"{it.get('order_no','?')}/{it.get('username','?')}")
    return {"data_len": len(items), "sample": heads}


def run(name, method, path, **kwargs):
    print(f"\n===== {name} =====")
    url = BASE + path
    try:
        r = s.request(method, url, timeout=10, **kwargs)
    except requests.RequestException as e:
        print(f"  ERROR: {e}")
        return None
    print(f"  status_code: {r.status_code}")
    if r.status_code >= 500:
        print("  *** 5xx response.text ***")
        print(r.text[:2000])
        return r
    try:
        j = r.json()
    except ValueError:
        print(f"  非 JSON 响应: {r.text[:300]}")
        return r
    print(f"  success: {j.get('success')}")
    print(f"  摘要: {json.dumps(summarize(j), ensure_ascii=False)}")
    return j


# 1. 登录
print("===== 1. 登录 admin/123 =====")
lr = s.post(f"{BASE}/api/login", json={"username": "admin", "password": "123"}, timeout=10)
print(f"  status_code: {lr.status_code}")
try:
    lj = lr.json()
except ValueError:
    print(f"  非 JSON 响应: {lr.text[:300]}")
    lj = {}
print(f"  success: {lj.get('success')}")
print(f"  cookies: {dict(s.cookies)}")

# 2. 基础请求
run("2. 基础请求（不带新参数）", "GET", "/api/admin/payment-orders")

# 3. scope=username&q=杨
run("3. scope=username&q=杨", "GET", "/api/admin/payment-orders",
    params={"scope": "username", "q": "杨"})

# 4. scope=amount&q=100
run("4. scope=amount&q=100", "GET", "/api/admin/payment-orders",
    params={"scope": "amount", "q": "100"})

# 5. 时间筛选
run("5. date_from=2025-01-01&date_to=2025-12-31", "GET", "/api/admin/payment-orders",
    params={"date_from": "2025-01-01", "date_to": "2025-12-31"})

# 6. per_page=50
run("6. per_page=50", "GET", "/api/admin/payment-orders",
    params={"per_page": 50})

print("\n===== 完成 =====")
