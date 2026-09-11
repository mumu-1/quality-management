# -*- coding: utf-8 -*-
"""查看当前外网隧道地址 + 生成手机扫码二维码 + 外网自检
用法：python scripts/tunnel_status.py
说明：适用于 Cloudflare 隧道（临时地址或固定域名）。会把地址写到桌面 QMS外网地址.txt，
      并生成 QMS外网二维码.png，方便发给同事/手机上扫码打开。
"""
import json
import os
import re
import socket
import ssl
import subprocess
import sys
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXE = os.path.join(os.path.expanduser("~"), "cloudflared", "cloudflared.exe")
DESK = os.path.join(os.path.expanduser("~"), "Desktop")
OUT_LOG = os.path.join(os.environ.get("TEMP", "."), "cf_tunnel_out.txt")
NAMED_CFG = os.path.join(os.path.expanduser("~"), ".cloudflared", "config.yml")


def running():
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='cloudflared.exe'\" | "
             "Select-Object -ExpandProperty CommandLine"],
            capture_output=True, text=True, timeout=30).stdout
        return [ln.strip() for ln in out.splitlines() if "cloudflared" in ln]
    except Exception:
        return []


def named_hostname():
    """从固定域名隧道配置里读主机名"""
    if not os.path.exists(NAMED_CFG):
        return ""
    try:
        txt = open(NAMED_CFG, encoding="utf-8").read()
        m = re.search(r"hostname:\s*([^\s]+)", txt)
        return ("https://" + m.group(1)) if m else ""
    except Exception:
        return ""


def quick_url():
    """从临时隧道输出里抓地址"""
    if os.path.exists(OUT_LOG):
        try:
            txt = open(OUT_LOG, encoding="utf-8", errors="ignore").read()
            m = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
            if m:
                return m[-1]
        except Exception:
            pass
    p = os.path.join(os.environ.get("TEMP", "."), "qms_public_url.txt")
    if os.path.exists(p):
        return open(p, encoding="utf-8").read().strip()
    return ""


def lan_url():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("223.5.5.5", 80))
        ip = s.getsockname()[0]
        s.close()
        return f"http://{ip}:8000"
    except Exception:
        return ""


def check_health(base):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(urllib.request.Request(base + "/api/health",
                                                           headers={"User-Agent": "probe"}),
                                    timeout=25, context=ctx) as r:
            b = r.read().decode("utf-8", "ignore")
            return ('"ok"' in b), b[:80]
    except Exception as e:
        return False, str(e)[:100]


print("═" * 60)
print("QMS 外网隧道状态")
print("═" * 60)

procs = running()
print(f"隧道进程：{'运行中（' + str(len(procs)) + ' 个）' if procs else '❌ 未运行'}")
for p in procs:
    print("   ", p[:110])

fixed = named_hostname()
quick = quick_url()
print(f"\n固定域名地址：{fixed or '（未配置固定域名隧道）'}")
print(f"临时地址：{quick or '（无记录）'}")

lan = lan_url()
print(f"厂内局域网地址：{lan or '（未取到）'}")

targets = []
if fixed:
    targets.append(("固定域名", fixed))
if quick:
    targets.append(("临时地址", quick))
if lan:
    targets.append(("厂内WiFi", lan))

if not targets:
    print("\n❌ 没有可用地址。启动隧道：scripts\\start_public_tunnel.bat（临时）")
    sys.exit(1)

print("\n可达性自检：")
alive = []
for name, u in targets:
    ok, msg = check_health(u)
    print(f"  {'✔' if ok else '✘'} {name:8s} {u}  {msg}")
    if ok and not u.startswith("http://192."):
        alive.append((name, u))

if not os.path.isdir(DESK):
    DESK = os.path.join(os.path.expanduser("~"), "Documents")
try:
    txt_path = os.path.join(DESK, "QMS外网地址.txt")
    with open(txt_path, "w", encoding="utf-8") as f:
        f.write("生产质量管理系统 · 外网访问地址\n" + "=" * 36 + "\n\n")
        for name, u in targets:
            f.write(f"{name}：{u}\n")
        f.write("\n备注：临时地址在隧道重启后会变化；固定域名地址长期有效。\n")
    print(f"\n✔ 地址已写入：{txt_path}")
except Exception as e:
    print("写地址文件失败:", str(e)[:80])

if alive:
    try:
        import qrcode
        name, u = alive[0]
        img = qrcode.make(u, box_size=9, border=2)
        qp = os.path.join(DESK, "QMS外网二维码.png")
        img.save(qp)
        print(f"✔ 外网二维码已生成（{name}）：{qp}")
    except Exception as e:
        print("生成二维码失败:", str(e)[:80])

print("═" * 60)
