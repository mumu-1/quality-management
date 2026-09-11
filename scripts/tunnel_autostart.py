# -*- coding: utf-8 -*-
"""外网隧道守护：启动 Cloudflare 隧道（临时地址模式），
自动把当前公网地址写到桌面并生成二维码；隧道断了会自动重连。
用于"开机自启"，让外网访问长期可用。

用法：python scripts/tunnel_autostart.py            （前台运行，关窗口即停）
      （开机自启用 install_autostart.bat 安装到启动文件夹）
"""
import os
import re
import ssl
import subprocess
import time
import urllib.request

EXE = os.path.join(os.path.expanduser("~"), "cloudflared", "cloudflared.exe")
TMP = os.environ.get("TEMP", ".")
OUT = os.path.join(TMP, "cf_tunnel_autostart_out.txt")
URLFILE = os.path.join(TMP, "qms_public_url.txt")
DESK = os.path.join(os.path.expanduser("~"), "Desktop")


def wait_local_app(timeout=120):
    """等本机服务起来（开机时系统可能还在启动）"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen("http://localhost:8000/api/health", timeout=5) as r:
                if b'"ok"' in r.read():
                    return True
        except Exception:
            time.sleep(3)
    return False


def note_url(url):
    try:
        with open(URLFILE, "w", encoding="utf-8") as f:
            f.write(url)
    except Exception:
        pass
    try:
        d = DESK if os.path.isdir(DESK) else os.path.join(os.path.expanduser("~"), "Documents")
        with open(os.path.join(d, "QMS外网地址.txt"), "w", encoding="utf-8") as f:
            f.write("生产质量管理系统 · 外网访问地址（开机自动更新）\n" + "=" * 40 + "\n\n")
            f.write(f"外网（4G/5G、外面）：{url}\n")
            f.write("厂内（同一 WiFi）：http://<本机局域网IP>:8000\n\n")
            f.write("注意：这是 Cloudflare 临时地址，每次重启会变化，以本文件为准。\n")
            f.write("需要长期固定地址：见《交付文档-外网访问指南.md》路线 A2（固定域名）。\n")
        try:
            import qrcode
            qrcode.make(url, box_size=9, border=2).save(os.path.join(d, "QMS外网二维码.png"))
        except Exception:
            pass
        print(f"[{time.strftime('%H:%M:%S')}] 地址已更新到桌面：{url}")
    except Exception as e:
        print("写地址文件失败:", str(e)[:80])


if not os.path.exists(EXE):
    print(f"[X] 未找到 {EXE}，请先运行：python scripts\\download_cloudflared.py")
    raise SystemExit(1)

print("等待本机服务启动…")
if not wait_local_app():
    print("[!] 本机 8000 未就绪，仍继续启动隧道（服务起来后即可访问）")
print("启动隧道（临时地址模式）…按 Ctrl+C 结束")

while True:                                  # 断了自动重连
    f = open(OUT, "w", encoding="utf-8", errors="ignore")
    p = subprocess.Popen([EXE, "tunnel", "--url", "http://localhost:8000", "--no-autoupdate"],
                         stdout=f, stderr=subprocess.STDOUT,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    url = ""
    for _ in range(40):                      # 最多等 80 秒拿地址
        time.sleep(2)
        f.flush()
        try:
            txt = open(OUT, encoding="utf-8", errors="ignore").read()
        except Exception:
            txt = ""
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", txt)
        if m:
            url = m.group(0)
            break
        if p.poll() is not None:
            break
    if url:
        note_url(url)
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        try:
            with urllib.request.urlopen(url + "/api/health", timeout=25, context=ctx) as r:
                print(f"[{time.strftime('%H:%M:%S')}] 外网自检：{'✔ 可达' if b'ok' in r.read() else '？'}")
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] 外网自检失败（可能仍在生效）：{str(e)[:60]}")
    p.wait()                                  # 隧道退出 → 重连
    print(f"[{time.strftime('%H:%M:%S')}] 隧道断开，5 秒后重连…")
    time.sleep(5)
