# -*- coding: utf-8 -*-
"""下载 cloudflared（Windows）到 ~/cloudflared/，多镜像回退、断点续传
用法：python scripts/download_cloudflared.py
"""
import os
import time
import urllib.request

DIR = os.path.join(os.path.expanduser("~"), "cloudflared")
os.makedirs(DIR, exist_ok=True)
EXE = os.path.join(DIR, "cloudflared.exe")
TARGET = ("https://github.com/cloudflare/cloudflared/releases/latest/download/"
          "cloudflared-windows-amd64.exe")
MIRRORS = [("gh-proxy", "https://gh-proxy.com/" + TARGET),
           ("ghfast", "https://ghfast.top/" + TARGET),
           ("直连 GitHub", TARGET)]

if os.path.exists(EXE) and os.path.getsize(EXE) > 20_000_000:
    print(f"✔ 已存在：{EXE}（{os.path.getsize(EXE)/1048576:.1f} MB）")
    raise SystemExit(0)

for name, url in MIRRORS:
    part = EXE + ".part"
    have = os.path.getsize(part) if os.path.exists(part) else 0
    print(f"── 尝试 {name}")
    r = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    if have:
        r.add_header("Range", f"bytes={have}-")
    try:
        t0 = time.time()
        with urllib.request.urlopen(r, timeout=40) as resp, open(part, "ab") as f:
            got = have
            while True:
                chunk = resp.read(262144)
                if not chunk:
                    break
                f.write(chunk)
                got += len(chunk)
                if got % (5 * 1048576) < 262144:
                    print(f"   {got/1048576:6.1f} MB  ({got/max(0.001, time.time()-t0)/1048576:.2f} MB/s)")
        if os.path.getsize(part) > 20_000_000:
            os.replace(part, EXE)
            print(f"✔ 下载完成：{EXE}")
            raise SystemExit(0)
        print("   不完整，换下一个源")
    except SystemExit:
        raise
    except Exception as e:
        print(f"   失败：{str(e)[:110]}")
        time.sleep(2)
print("✘ 全部源失败。可手动下载 cloudflared-windows-amd64.exe 放到 "
      f"{DIR}\\cloudflared.exe")
