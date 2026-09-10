# -*- coding: utf-8 -*-
"""公网暴露前体检：逐项检查，给出"能不能上公网"的结论
用法：python scripts/preflight_public.py            （基础体检）
      python scripts/preflight_public.py --with-lock-test   （额外验证登录锁定是否生效）
说明：会用 123456 等常见弱口令尝试登录内置演示账号（每账号仅 1 次，不会触发锁定）。
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("QMS_BASE", "http://localhost:8000")
WEAK_TRY = ["123456", "admin123", "12345678"]
PROBE_USERS = ["admin", "qm", "qc", "prodlead", "store", "buyer", "sampler", "boss"]
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OK, BAD, WARN = [], [], []


def line(kind, name, detail=""):
    icon = {"ok": "✔", "bad": "✘", "warn": "⚠"}[kind]
    print(f"  {icon} {name}" + (f"   {detail}" if detail else ""))
    (OK if kind == "ok" else BAD if kind == "bad" else WARN).append(name)


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
    except Exception as e:
        return 0, str(e)[:120], {}


print("═" * 64)
print("公网暴露前体检（QMS）")
print("═" * 64)

# 1) 服务在线
st, txt, _ = raw("/api/health")
line("ok" if st == 200 else "bad", "服务在线", f"HTTP {st}")

# 2) 监听地址
try:
    import subprocess
    out = subprocess.run(["powershell", "-NoProfile", "-Command",
                          "(Get-NetTCPConnection -State Listen | Where-Object {$_.LocalPort -in 8000,8080} "
                          "| Select-Object -ExpandProperty LocalAddress) -join ','"],
                         capture_output=True, text=True, timeout=25).stdout.strip()
    line("ok" if "0.0.0.0" in out or "::" in out else "warn",
         "服务监听地址", out or "未取到（端口可能非 8000）")
except Exception as e:
    line("warn", "服务监听地址", str(e)[:60])

# 3) 弱口令扫描
print("\n── 弱口令扫描（每个账号只试 1 次，不会触发锁定）──")
weak_users = []
for u in PROBE_USERS:
    for p in WEAK_TRY:
        st, txt, _ = raw("/api/auth/login", method="POST", body={"username": u, "password": p})
        if st == 200:
            weak_users.append(f"{u}/{p}")
            break
        if st in (429, 403):
            break
if weak_users:
    line("bad", f"发现弱口令账号 {len(weak_users)} 个", "、".join(weak_users))
    print("      → 先执行：python scripts/set_password.py harden")
else:
    line("ok", "未发现常见弱口令", "（已试 123456/admin123/12345678）")

# 4) 登录锁定是否生效
if "--with-lock-test" in sys.argv:
    print("\n── 登录锁定验证 ──")
    tst = "locktest_" + str(abs(hash(BASE)) % 10000)
    codes = []
    for i in range(6):
        st, _, _ = raw("/api/auth/login", method="POST", body={"username": tst, "password": "x" * 8})
        codes.append(st)
    line("ok" if 429 in codes else "bad", "连错会被锁定", f"状态码序列 {codes}")
    st, txt, _ = raw("/api/auth/login", method="POST", body={"username": "admin", "password": "123456"})
    if st == 200:
        t = json.loads(txt)["token"]
        raw("/api/admin/login-locks/clear", t=t, method="POST")
        line("ok", "锁定已清空（管理员解锁接口可用）")
else:
    line("warn", "登录锁定未检测", "需要时加 --with-lock-test")

# 5) CORS 是否收紧
st, txt, hdr = raw("/api/health", origin="http://evil.example.com")
acao = hdr.get("access-control-allow-origin", hdr.get("Access-Control-Allow-Origin", ""))
line("ok" if acao not in ("*", "http://evil.example.com") else "bad",
     "跨域策略已收紧", f"allow-origin={acao or '（无，仅同源）'}")

# 6) HTTPS
https_base = os.environ.get("QMS_PUBLIC_BASE", "")
if https_base.startswith("https://"):
    line("ok", "对外使用 HTTPS", https_base)
else:
    line("warn", "对外未启用 HTTPS", "公网务必用 HTTPS（隧道自带，或配证书）；HTTP 明文会泄露密码")

# 7) 演示数据 / 备份 / 敏感文件
import glob
pwd_tables = glob.glob(os.path.join(ROOT, "账号*密码表*.csv"))
line("bad" if pwd_tables else "ok", "无遗留密码表文件",
     "、".join(os.path.basename(x) for x in pwd_tables) if pwd_tables else "")
gi = os.path.join(ROOT, ".gitignore")
gi_txt = open(gi, encoding="utf-8").read() if os.path.exists(gi) else ""
line("ok" if ("密码表" in gi_txt or "*.csv" in gi_txt) else "warn",
     ".gitignore 已忽略密码表/导出文件")
bk = os.path.join(ROOT, "scripts", "daily_backup.bat")
line("ok" if os.path.exists(bk) else "warn", "每日备份脚本在位",
     "scripts/daily_backup.bat" if os.path.exists(bk) else "未找到，公网运行前请配置备份")

# 8) 审计日志
st, txt, _ = raw("/api/auth/login", method="POST", body={"username": "admin", "password": "123456"})
if st == 200:
    tok = json.loads(txt)["token"]
    st2, txt2, _ = raw("/api/audit-logs?days=1", t=tok)
    d = json.loads(txt2) if st2 == 200 else {}
    n = len(d.get("rows", []))
    has_login = any(x.get("action") in ("login", "login_fail") for x in d.get("rows", []))
    line("ok" if has_login else "warn", "登录行为已留痕", f"近 1 天 {n} 条日志")
    st3, txt3, _ = raw("/api/production-lots", t=tok)
    lots = len(json.loads(txt3)) if st3 == 200 else -1
    line("warn" if lots > 0 else "ok", "演示数据仍在库中" if lots > 0 else "库中无演示数据",
         f"{lots} 个生产批（正式上线前请清空演示数据）" if lots > 0 else "")
else:
    line("warn", "审计日志未检测", "管理员登录失败（可能已改密）")

print("\n" + "═" * 64)
print(f"结论：{len(OK)} 项正常 / {len(WARN)} 项注意 / {len(BAD)} 项必须处理")
if BAD:
    print("❌ 还有必须处理的问题，先处理完再暴露到公网：")
    for b in BAD:
        print("   -", b)
else:
    print("✅ 可选：现在可以暴露到公网（务必使用 HTTPS，并确认备份已开启）")
print("═" * 64)
sys.exit(1 if BAD else 0)
