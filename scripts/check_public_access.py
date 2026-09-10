# -*- coding: utf-8 -*-
"""外网访问自检：给一个公网地址做体检（是否可达、是否 HTTPS、登录接口是否正常、登录限速是否生效）
用法：
  python scripts/check_public_access.py https://xxx.trycloudflare.com
  python scripts/check_public_access.py http://106.43.139.235:18080
"""
import json
import ssl
import sys
import urllib.error
import urllib.request

if len(sys.argv) < 2:
    print(__doc__)
    sys.exit(2)
BASE = sys.argv[1].rstrip("/")
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE
OK, BAD, WARN = [], [], []


def line(kind, name, detail=""):
    icon = {"ok": "✔", "bad": "✘", "warn": "⚠"}[kind]
    print(f"  {icon} {name}" + (f"   {detail}" if detail else ""))
    (OK if kind == "ok" else BAD if kind == "bad" else WARN).append(name)


def req(path, method="GET", body=None, timeout=20):
    r = urllib.request.Request(BASE + path,
                               data=json.dumps(body).encode() if body is not None else None,
                               method=method, headers={"User-Agent": "qms-check"})
    if body is not None:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=timeout, context=CTX) as resp:
            return resp.status, resp.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")
    except Exception as e:
        return 0, str(e)[:150]


print("═" * 60)
print(f"外网访问自检：{BASE}")
print("═" * 60)

st, body = req("/api/health")
line("ok" if st == 200 and '"ok"' in body else "bad", "外网可达（/api/health）", f"HTTP {st} {body[:70]}")

st, body = req("/")
line("ok" if st == 200 and len(body) > 1000 else "bad", "首页可打开",
     f"HTTP {st} {len(body)} 字符" + ("（是本系统）" if "生产质量" in body else ""))
line("ok" if BASE.startswith("https://") else "warn",
     "使用 HTTPS（必须，否则密码明文外传）", BASE.split(":")[0])

st, body = req("/api/auth/login", method="POST", body={"username": "nosuchuser__probe", "password": "x" * 10})
line("ok" if st in (401, 429) else "warn", "登录接口正常（错误口令被拒）", f"HTTP {st}")

codes = []
for _ in range(6):
    st, _ = req("/api/auth/login", method="POST", body={"username": "rateprobe__x", "password": "y" * 10})
    codes.append(st)
line("ok" if 429 in codes else "bad", "登录限速生效（防爆破）", f"状态码 {codes}")

st, body = req("/api/coas")
line("ok" if st in (401, 403) else "bad", "接口需登录（未登录访问被拒）", f"HTTP {st}")

st, body = req("/coa/NOPE-000?k=x")
line("ok" if st in (403, 404) else "warn", "客户报告页受口令保护", f"HTTP {st}")

print("─" * 60)
print(f"结论：{len(OK)} 项正常 / {len(WARN)} 项注意 / {len(BAD)} 项必须处理")
if BAD:
    print("❌ 问题项：", "、".join(BAD))
else:
    print("✅ 外网访问基本可用（注意：登录限速被触发时会锁 10 分钟，属正常保护）")
print("═" * 60)
sys.exit(1 if BAD else 0)
