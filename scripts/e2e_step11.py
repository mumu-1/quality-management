# -*- coding: utf-8 -*-
"""第11步 e2e 验收：公网暴露安全加固（登录限速/锁定 + 登录审计 + CORS + 运维工具）
覆盖：
 1) 登录审计：成功/失败都留痕
 2) 登录锁定：同一账号连错 5 次 → 第 6 次 429；锁定期内即使密码正确也 429
 3) 锁定不影响其他账号（同 IP 不同账号仍可登录）
 4) 管理员解锁接口：可查看锁定列表、清空后即可正常登录
 5) CORS 收紧：不向任意来源放开（不带通配）
 6) 运维工具就位：set_password.py / preflight_public.py 存在且能干跑
运行: python scripts/e2e_step11.py（先启动服务；跑完请重置演示库）
注意：本脚本会创建临时账号做锁定测试，结束时自动解锁并清理。
"""
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

BASE = "http://localhost:8000"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TS = str(int(time.time()) % 100000)
PASS, FAIL = [], []


def raw(path, t="", method="GET", body=None, origin=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if t:
        req.add_header("token", t)
    if origin:
        req.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read().decode("utf-8", "ignore"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)


def call(path, t="", method="GET", body=None):
    st, txt, _ = raw(path, t, method, body)
    try:
        d = json.loads(txt)
    except Exception:
        return {"_err": st, "_text": txt[:200]}
    if st >= 400 and isinstance(d, dict) and "_err" not in d:
        d["_err"] = st
    return d


def check(n, c, x=""):
    (PASS if c else FAIL).append(n)
    print(("  ✔ " if c else "  ✘ ") + n + ("  " + x if x else ""))


def login(u, p="123456"):
    return call("/api/auth/login", method="POST", body={"username": u, "password": p})


AT = login("admin").get("token", "")
check("管理员登录正常", bool(AT))

print("══ 1. 登录审计 ══")
al = call("/api/audit-logs?days=1&limit=500", t=AT)
acts = [x["action"] for x in al.get("rows", [])]
check("登录成功有留痕", "login" in acts, f"动作集合 {sorted(set(acts))[:6]}")

print("══ 2. 登录锁定（防公网暴力破解）══")
# 用临时账号测锁定，避免影响演示账号
tmp_user = f"lockprobe{TS}"
PASSWORD = "Init#12345"
st, _, _ = raw("/api/admin/users", AT, "POST",
               {"username": tmp_user, "password": PASSWORD, "real_name": "锁定测试",
                "role_key": "worker"})
created = st in (200, 201)
check("创建临时测试账号", created, f"HTTP {st}")
codes = []
for i in range(5):
    st, _, _ = raw("/api/auth/login", method="POST", body={"username": tmp_user, "password": "wrong1234"})
    codes.append(st)
check("前 4 次错误返回 401", codes[:4] == [401] * 4, str(codes))
check("第 5 次错误即触发锁定（429）", codes[4] == 429, str(codes))
st6, txt6, _ = raw("/api/auth/login", method="POST", body={"username": tmp_user, "password": "wrong1234"})
check("★ 第 6 次被锁定（429）", st6 == 429, f"HTTP {st6} {txt6[:60]}")
st7, _, _ = raw("/api/auth/login", method="POST", body={"username": tmp_user, "password": PASSWORD})
check("★ 锁定期内正确密码也被拒（429）", st7 == 429, f"HTTP {st7}")
st8, _, _ = raw("/api/auth/login", method="POST", body={"username": "admin", "password": "123456"})
check("★ 锁定不影响其他账号（同 IP 仍可登录）", st8 == 200, f"HTTP {st8}")
st9, _, _ = raw("/api/auth/login", method="POST", body={"username": tmp_user.upper(), "password": PASSWORD})
check("换账号名大小写也无法绕过", st9 in (401, 429), f"HTTP {st9}")
al2 = call("/api/audit-logs?days=1&limit=500", t=AT)
check("★ 登录失败有留痕（含来源 IP）",
      any(x["action"] == "login_fail" for x in al2.get("rows", [])),
      next((x["target"] for x in al2.get("rows", []) if x["action"] == "login_fail"), ""))

print("══ 3. 锁定查看与解锁（运维）══")
locks = call("/api/admin/login-locks", t=AT)
check("可查看锁定列表", "rows" in locks and "policy" in locks, f"当前锁定 {locks.get('total')} 项")
check("锁定策略随环境可配", locks.get("policy", {}).get("账号+IP 连错次数") == 5)
check("锁定列表含该测试账号",
      any(tmp_user in x["key"] for x in locks.get("rows", [])),
      next((x["key"] for x in locks.get("rows", []) if tmp_user in x["key"]), ""))
cl = call("/api/admin/login-locks/clear", t=AT, method="POST", body={})
check("可清空全部锁定", cl.get("ok") is True and cl.get("cleared", 0) >= 1, str(cl))
st10, _, _ = raw("/api/auth/login", method="POST", body={"username": tmp_user, "password": PASSWORD})
check("★ 解锁后该账号可正常登录", st10 == 200, f"HTTP {st10}")
check("非管理员不能查看锁定（检验员 403）",
      call("/api/admin/login-locks", t=login("qc").get("token", "")).get("_err") == 403)

print("══ 4. CORS 收紧 ══")
st, txt, hdr = raw("/api/health", origin="http://evil.example.com")
acao = hdr.get("access-control-allow-origin", hdr.get("Access-Control-Allow-Origin", ""))
check("★ 不向任意来源放开跨域", acao != "*" and "evil.example.com" not in acao,
      f"allow-origin={acao or '（无，仅同源）'}")

print("══ 5. 运维工具就位 ══")
sp = os.path.join(ROOT, "scripts", "set_password.py")
pf = os.path.join(ROOT, "scripts", "preflight_public.py")
check("改密工具存在", os.path.exists(sp))
check("暴露前体检工具存在", os.path.exists(pf))
r = subprocess.run([sys.executable, sp, "list"], capture_output=True, text=True, timeout=90,
                   encoding="utf-8", errors="ignore", cwd=ROOT)
check("改密工具能列账号", "账号" in (r.stdout or "") and "role" not in (r.stdout or "")[:60],
      (r.stdout or "").splitlines()[1][:60] if len((r.stdout or "").splitlines()) > 1 else "")
r2 = subprocess.run([sys.executable, sp, "set", tmp_user, "weak"], capture_output=True,
                    text=True, timeout=90, encoding="utf-8", errors="ignore", cwd=ROOT)
check("改密工具拒绝弱口令", r2.returncode == 2 and "不合格" in (r2.stdout or ""), (r2.stdout or "").strip()[:60])
check("公网体检脚本可运行（含弱口令扫描）",
      "公网暴露前体检" in (subprocess.run([sys.executable, pf], capture_output=True, text=True,
                                          timeout=180, encoding="utf-8", errors="ignore",
                                          cwd=ROOT).stdout or ""))

print("══ 6. 收尾：清理测试账号 ══")
if created:
    ul = call("/api/admin/users?keyword=" + tmp_user, t=AT)
    row = next((x for x in (ul if isinstance(ul, list) else []) if x.get("username") == tmp_user), None)
    if row:
        st, _, _ = raw(f"/api/admin/users/{row['id']}", AT, "PUT", {"enabled": False})
        check("停用并清理测试账号", st == 200, f"HTTP {st} uid={row['id']}")
    else:
        check("清理测试账号", False, "未找到临时账号")
call("/api/admin/login-locks/clear", t=AT, method="POST", body={})

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第11步（公网暴露安全加固）验收全部通过")
