# -*- coding: utf-8 -*-
"""
全流程验证脚本 - verify_all.py
验证 admin/白杨杨 用户状态、HTML 结构、API 搜索参数、密码哈希等。
注意：/admin/payment-audit 路由已重定向到 /admin/payment/orders（重构版本）。
"""
import sys
import json
import re
import os

sys.stdout.reconfigure(encoding='utf-8')

import requests
from bs4 import BeautifulSoup

BASE = "http://localhost:5001"
ADMIN_USER = "admin"
ADMIN_PASS = "123456"
USER_USER = "白杨杨"
USER_PASS = "123456"

PASS = 0
FAIL = 0
INFO = 0


def check(label, ok, evidence=""):
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")
    if evidence:
        ev = str(evidence)
        if len(ev) > 500:
            ev = ev[:500] + "...(截断)"
        print(f"         → {ev}")


def info(label, detail=""):
    global INFO
    INFO += 1
    print(f"  [INFO] {label}")
    if detail:
        d = str(detail)
        if len(d) > 500:
            d = d[:500] + "...(截断)"
        print(f"         → {d}")


# ============================================================
# Step 1 — admin 强制改密处理
# ============================================================
print("=" * 60)
print("Step 1 — admin 强制改密处理")
print("=" * 60)

admin_sess = requests.Session()

try:
    r = admin_sess.post(f"{BASE}/api/login",
                        json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=10)
    data = r.json()
    check(f"POST /api/login admin ({ADMIN_USER}/{ADMIN_PASS})",
          r.status_code == 200 and data.get("success") is not False,
          f"status={r.status_code}")
except Exception as e:
    check("POST /api/login admin", False, str(e))
    sys.exit(1)

must_change = data.get("data", {}).get("must_change_password", False)
print(f"  must_change_password = {must_change}")

if must_change:
    print("  → must_change_password=True，执行改密（密码不变，仅消除状态）")
    r = admin_sess.post(f"{BASE}/api/change-password",
                        json={"old_password": ADMIN_PASS, "new_password": ADMIN_PASS},
                        timeout=10)
    check("POST /api/change-password (密码不变)",
          r.status_code in (200, 201, 202),
          f"status={r.status_code}, body={r.text[:300]}")
else:
    print("  → must_change_password 已是 False，跳过改密步骤")

# 1c 再次登录确认
r = admin_sess.post(f"{BASE}/api/login",
                    json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=10)
data2 = r.json()
must_change2 = data2.get("data", {}).get("must_change_password")
check("再次登录 must_change_password=False",
      must_change2 is False,
      f"must_change_password={must_change2}")
print(f"  admin username={data2.get('data', {}).get('username')}, "
      f"role={data2.get('data', {}).get('role')}")


# ============================================================
# Step 2 — 管理员支付审核页面 HTML 结构验证
# ============================================================
print()
print("=" * 60)
print("Step 2 — 管理员支付审核页面 HTML 结构验证")
print("=" * 60)

AUDIT_PATH = "/admin/payment-audit"
try:
    r = admin_sess.get(f"{BASE}{AUDIT_PATH}", timeout=10, allow_redirects=False)
    status_no_rd = r.status_code
    loc = r.headers.get("Location", "")
    info(f"GET {AUDIT_PATH} (no redirect)",
         f"status={status_no_rd}, Location={loc}")

    r2 = admin_sess.get(f"{BASE}{AUDIT_PATH}", timeout=10, allow_redirects=True)
    final_url = r2.url.replace(BASE, "")
    check(f"GET {AUDIT_PATH} 最终到达有效页面",
          r2.status_code == 200,
          f"final_url={final_url}, len={len(r2.text)}")
    audit_html = r2.text
except Exception as e:
    check(f"GET {AUDIT_PATH}", False, str(e))
    audit_html = ""

info(f"注意: /admin/payment-audit 路由现状",
     "app.py 第 9215 行已 redirect('/admin/payment/orders'); "
     "实际服务页面来自 templates/admin/payment/orders.html（重构版）；"
     "旧模板 templates/admin/payment_audit.html 仍存在但不再被路由使用。")

if audit_html:
    bad_markers = ["UndefinedError", "Traceback", "jinja2.exceptions",
                   "jinja2.UndefinedError", "TemplateSyntaxError"]
    found_bad = [m for m in bad_markers if m in audit_html]
    check("无 Jinja 模板错误残留",
          not found_bad,
          f"发现: {found_bad}" if found_bad else "干净 ✓")

    soup = BeautifulSoup(audit_html, "html.parser")

    # ---- 任务描述里列出的元素（旧版 payment_audit.html 风格）----
    # 新版 orders.html 里这些多数不存在，如实报告
    filter_bar = soup.find(class_=re.compile(r"filter-search-bar"))
    check('[旧模板风格] class="filter-search-bar" 存在', filter_bar is not None)

    scope = soup.find("select", id="searchScope")
    check('[旧模板风格] select#searchScope 存在', scope is not None,
          f"options={[o.get_text(strip=True) for o in scope.find_all('option')]}" if scope else "(新版页面已移除 scope 下拉)")

    check('[旧模板风格] input#dateFrom', soup.find("input", id="dateFrom") is not None)
    check('[旧模板风格] input#dateTo', soup.find("input", id="dateTo") is not None)

    search_btn = soup.find("button", attrs={"onclick": re.compile(r"doSearch")})
    check('[旧模板风格] 搜索按钮 onclick="doSearch()"', search_btn is not None)

    reset_btn = soup.find("button", attrs={"onclick": re.compile(r"resetSearch")})
    check('[旧模板风格] 重置按钮 onclick="resetSearch()"', reset_btn is not None)

    perpage_selects = soup.select(".pagination-perpage select")
    check('[旧模板风格] pagination-perpage select 存在', len(perpage_selects) > 0)

    data_table_attr = soup.find("table", attrs={"data-table": True})
    check('[旧模板风格] table[data-table] 存在', data_table_attr is not None)

    # ---- 新版 orders.html 实际元素 ----
    info("[重构版 orders.html] 实际页面结构")
    filter_group = soup.find(class_="filter-group")
    check('[新版] .filter-group (状态按钮组)', filter_group is not None,
          f"按钮数={len(filter_group.find_all('button', class_='filter-btn')) if filter_group else 0}")

    data_table_class = soup.find("table", class_="data-table")
    check('[新版] table.data-table (class 而非 attr)', data_table_class is not None)

    pagination_div = soup.find(id="paginationContainer")
    check('[新版] #paginationContainer 存在', pagination_div is not None)

    # 列出 .filter-btn 状态筛选按钮
    if filter_group:
        btns = filter_group.find_all("button", class_="filter-btn")
        for b in btns:
            info(f"filter-btn",
                 f"data-status={b.get('data-status')}, text={b.get_text(strip=True)}")
else:
    print("  无 HTML 可解析，跳过结构检查")

# ---- 也静态解析旧模板 payment_audit.html 文件，看任务描述元素是否在文件里 ----
print()
print("  --- 静态检查：旧模板文件 payment_audit.html ---")
old_tpl = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "templates", "admin", "payment_audit.html")
if os.path.exists(old_tpl):
    with open(old_tpl, "r", encoding="utf-8") as f:
        old_html = f.read()
    old_soup = BeautifulSoup(old_html, "html.parser")

    check("[旧模板文件] class='filter-search-bar' 在 payment_audit.html 中",
          old_soup.find(class_=re.compile(r"filter-search-bar")) is not None)

    scope = old_soup.find("select", id="searchScope")
    check("[旧模板文件] select#searchScope 在 payment_audit.html 中",
          scope is not None)
    if scope:
        opts = [o.get_text(strip=True) for o in scope.find_all("option")]
        print(f"          → options: {opts}")

    check("[旧模板文件] input#dateFrom 在 payment_audit.html 中",
          old_soup.find("input", id="dateFrom") is not None)
    check("[旧模板文件] input#dateTo 在 payment_audit.html 中",
          old_soup.find("input", id="dateTo") is not None)
    check("[旧模板文件] doSearch() 在 payment_audit.html 中",
          "doSearch()" in old_html)
    check("[旧模板文件] resetSearch() 在 payment_audit.html 中",
          "resetSearch()" in old_html)
    check("[旧模板文件] pagination-perpage 在 payment_audit.html 中",
          "pagination-perpage" in old_html)
else:
    info("旧模板文件 payment_audit.html", "不存在")


# ============================================================
# Step 3 — 后端 API 搜索参数验证
# ============================================================
print()
print("=" * 60)
print("Step 3 — 后端 API 搜索参数验证")
print("=" * 60)

API_PATH = "/api/admin/payment-orders"


def api_call(label, params):
    global PASS, FAIL
    url = f"{BASE}{API_PATH}"
    try:
        r = admin_sess.get(url, params=params, timeout=10)
        print(f"  [{label}] GET {API_PATH} params={params}")
        print(f"           → status={r.status_code}, content-type={r.headers.get('Content-Type','')[:40]}")
        if r.status_code == 500:
            FAIL += 1
            print(f"           [FAIL] 500 错误！\n{r.text[:2000]}")
            return None
        if r.status_code != 200:
            FAIL += 1
            print(f"           [FAIL] status != 200, body={r.text[:300]}")
            return None
        if "json" not in r.headers.get("Content-Type", "").lower():
            FAIL += 1
            print(f"           [FAIL] 非 JSON 响应，body head={r.text[:200]}")
            return None

        data = r.json()
        success = data.get("success", r.status_code == 200)
        pagination = data.get("pagination") or {}
        pp = pagination.get("per_page") or pagination.get("perPage")
        items = data.get("data") or data.get("orders") or []
        first = items[0] if isinstance(items, list) and items else {}
        ono = None
        if isinstance(first, dict):
            ono = first.get("order_no") or first.get("orderNo") or first.get("id")
        print(f"           success={success}, pagination.per_page={pp}, "
              f"items_count={len(items) if isinstance(items, list) else 'N/A'}, "
              f"first_order_no={ono}")
        PASS += 1
        print(f"           [PASS]")
        return data
    except Exception as e:
        FAIL += 1
        print(f"           [FAIL] 异常: {e}")
        return None


api_call("scope=username&q=杨", {"scope": "username", "q": "杨"})
api_call("scope=amount&q=100", {"scope": "amount", "q": "100"})
api_call("date_from=2024-01-01", {"date_from": "2024-01-01"})
api_call("per_page=50", {"per_page": "50"})


# ============================================================
# Step 4 — 普通用户页面验证
# ============================================================
print()
print("=" * 60)
print("Step 4 — 普通用户页面验证")
print("=" * 60)

user_sess = requests.Session()

try:
    r = user_sess.post(f"{BASE}/api/login",
                       json={"username": USER_USER, "password": USER_PASS}, timeout=10)
    data = r.json()
    check(f"POST /api/login {USER_USER}",
          r.status_code == 200 and data.get("success") is not False,
          f"status={r.status_code}")
except Exception as e:
    check("POST /api/login 白杨杨", False, str(e))
    data = {}

user_role = data.get("data", {}).get("role")
check("role=user", user_role in ("user", "USER"), f"role={user_role}")

candidate_pages = [
    ("/", "首页"),
    ("/user/dashboard", "用户中心"),
    ("/user/payment", "充值页面"),
]

for path, name in candidate_pages:
    try:
        r = user_sess.get(f"{BASE}{path}", timeout=10, allow_redirects=True)
        final = r.url.replace(BASE, "")
        ok_status = r.status_code == 200
        has_html = len(r.text) > 500
        no_error = not any(x in r.text for x in [
            "UndefinedError", "Traceback", "jinja2.exceptions",
            "jinja2.UndefinedError", "TemplateSyntaxError"])
        check(f"GET {path} [{name}] 200+HTML+无错误",
              ok_status and has_html and no_error,
              f"status={r.status_code}, final={final}, len={len(r.text)}")

        if ok_status and has_html:
            soup = BeautifulSoup(r.text, "html.parser")
            pwt_all = soup.find_all(class_=re.compile(r"password-toggle"))
            pwd_inputs = soup.find_all("input", {"type": "password"})
            print(f"         → password inputs: {len(pwd_inputs)}, "
                  f"password-toggle elements: {len(pwt_all)}")
            for pwi in pwd_inputs:
                has_pwt = any("password-toggle" in c for c in pwi.get("class", []))
                ptype = pwi.find_parent(class_=re.compile(r"password-toggle"))
                print(f"           input#{pwi.get('id','')} class={pwi.get('class')} "
                      f"(has toggle class on self: {has_pwt}, parent .password-toggle: {ptype is not None})")
    except Exception as e:
        check(f"GET {path} [{name}]", False, str(e))


# ============================================================
# Step 5 — 密码哈希验证（SQLAlchemy）
# ============================================================
print()
print("=" * 60)
print("Step 5 — 密码哈希验证")
print("=" * 60)

try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.models import User

    db_candidates = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "database", "emby_manager.db"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "emby_manager.db"),
    ]
    db_path = None
    for c in db_candidates:
        if os.path.exists(c):
            db_path = c
            break
    if db_path is None:
        import glob as _glob
        found = _glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                        "**", "*.db"), recursive=True)
        db_path = found[0] if found else None

    if db_path is None:
        check("找到 SQLite 文件", False, "未找到 .db 文件")
    else:
        print(f"  数据库: {db_path}")
        engine = create_engine(f"sqlite:///{db_path}")
        Session = sessionmaker(bind=engine)
        dbsess = Session()

        for uid, expected_name, expected_role in [
                (1, "admin", ("admin",)),
                (2, "白杨杨", ("user",))]:
            u = dbsess.query(User).filter(User.id == uid).first()
            if u is None:
                check(f"User id={uid} 存在", False, "None")
                continue
            role_val = u.role.value if hasattr(u.role, "value") else str(u.role)
            # scrypt 哈希以 "scrypt:" 开头（Werkzeug 2.4+ 格式）
            hash_ok = u.password_hash.startswith("scrypt:")
            check(f"User id={uid} username={u.username}",
                  u.username == expected_name)
            check(f"User id={uid} role={role_val}",
                  role_val.lower() == expected_role[0].lower())
            check(f"User id={uid} password_hash 以 'scrypt:' 开头",
                  hash_ok,
                  f"前缀={u.password_hash[:40]}")
            print(f"    → id={uid} username={u.username} role={role_val}")
            print(f"      hash={u.password_hash[:80]}...")
            print(f"      must_change_password={u.must_change_password}")

        dbsess.close()
except Exception as e:
    import traceback
    traceback.print_exc()
    check("密码哈希验证", False, str(e))


# ============================================================
# 总结
# ============================================================
print()
print("=" * 60)
print(f"完成！PASS={PASS}  FAIL={FAIL}  INFO={INFO}   （总计 {PASS+FAIL+INFO} 项）")
print("=" * 60)
if FAIL > 0:
    print("⚠  存在 FAIL 项，请查看上方详细输出。")
    sys.exit(1)
else:
    print("✓ 所有检查通过")
    sys.exit(0)
