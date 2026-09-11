# -*- coding: utf-8 -*-
"""路线A2 固定域名向导（Cloudflare 隧道）：把本机 8000 发布到自己的域名，长期有效

用法：
  python scripts/cloudflare_setup.py --check              查看当前状态（是否已登录/已建隧道/配置）
  python scripts/cloudflare_setup.py qms.你的域名.com      一键创建隧道 + 绑定域名 + 写配置

前置条件（只需做一次，人工）：
  1) 有一个域名，并把它接入 Cloudflare（在 Cloudflare 里添加站点，改域名的 NS 到 Cloudflare）
  2) 在电脑上执行一次 cloudflared tunnel login（会打开浏览器，登录并授权）

本脚本会做的事：创建隧道 → 生成 ~/.cloudflared/config.yml → 绑定 DNS → 打印启动与自启方法。
"""
import os
import re
import subprocess
import sys

EXE = os.path.join(os.path.expanduser("~"), "cloudflared", "cloudflared.exe")
CF_DIR = os.path.join(os.path.expanduser("~"), ".cloudflared")
CFG = os.path.join(CF_DIR, "config.yml")
TUNNEL_NAME = "qms"
DOC = "《交付文档-外网访问指南.md》"


def run(args, timeout=120):
    p = subprocess.run([EXE] + args, capture_output=True, text=True, timeout=timeout,
                       encoding="utf-8", errors="ignore")
    return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()


def check_login():
    rc, out = run(["tunnel", "list"])
    if rc == 0:
        return True, out
    return False, out


def find_tunnel(out):
    for ln in out.splitlines():
        if TUNNEL_NAME in ln:
            m = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", ln)
            if m:
                return m.group(0)
    return ""


print("═" * 62)
print("路线A2 · Cloudflare 固定域名隧道向导")
print("═" * 62)

if not os.path.exists(EXE):
    print(f"✘ 未找到 {EXE}\n  请先运行：python scripts\\download_cloudflared.py")
    sys.exit(1)
print(f"✔ cloudflared 已就绪")

only_check = "--check" in sys.argv
hostname = next((a for a in sys.argv[1:] if not a.startswith("-")), "")

ok, out = check_login()
if not ok:
    print("\n✘ 尚未登录 Cloudflare 账号（隧道功能需要登录一次）")
    print("  请在电脑上执行下面这条命令，浏览器里点一下授权（只需一次）：")
    print(f'\n     "{EXE}" tunnel login\n')
    print("  登录成功后重新运行本脚本：")
    print(f"     python scripts\\cloudflare_setup.py {'你的域名，如 qms.example.com' if not hostname else hostname}")
    sys.exit(2)
print("✔ 已登录 Cloudflare 账号")
tunnel_id = find_tunnel(out)
print(f"{'✔ 已存在隧道 ' + TUNNEL_NAME + ' (' + tunnel_id[:8] + '…)' if tunnel_id else '· 还没有名为 ' + TUNNEL_NAME + ' 的隧道'}")
if os.path.exists(CFG):
    try:
        cur = re.search(r"hostname:\s*([^\s]+)", open(CFG, encoding="utf-8").read())
        print(f"✔ 已有配置，当前域名：{cur.group(1) if cur else '（未写 hostname）'}")
    except Exception:
        pass

if only_check:
    print("\n（--check 模式，不做修改）")
    sys.exit(0)

if not hostname:
    print(f"\n用法：python scripts\\cloudflare_setup.py <域名>")
    print("  例：python scripts\\cloudflare_setup.py qms.example.com")
    print("  需要先把该域名的 DNS 托管到 Cloudflare（改 NS 记录）")
    sys.exit(2)

if "." not in hostname:
    print(f"✘ 域名格式不对：{hostname}（应形如 qms.example.com）")
    sys.exit(2)

print(f"\n[1/4] 创建隧道 {TUNNEL_NAME} …")
if tunnel_id:
    print(f"     已存在，复用 {tunnel_id}")
else:
    rc, out = run(["tunnel", "create", TUNNEL_NAME])
    print("     " + out.replace("\n", "\n     ")[:400])
    if rc != 0:
        print("✘ 创建失败，请检查上面的输出")
        sys.exit(1)
    rc, out = run(["tunnel", "list"])
    tunnel_id = find_tunnel(out)
    if not tunnel_id:
        print("✘ 未能取到隧道 ID")
        sys.exit(1)

print(f"\n[2/4] 写配置 {CFG} …")
os.makedirs(CF_DIR, exist_ok=True)
cred = os.path.join(CF_DIR, f"{tunnel_id}.json")
cfg = (f"tunnel: {tunnel_id}\n"
       f"credentials-file: {cred}\n\n"
       f"ingress:\n"
       f"  - hostname: {hostname}\n"
       f"    service: http://localhost:8000\n"
       f"  - service: http_status:404\n")
with open(CFG, "w", encoding="utf-8", newline="\n") as f:
    f.write(cfg)
print("     已写入：")
for ln in cfg.splitlines():
    print("       " + ln)

print(f"\n[3/4] 绑定域名 {hostname} → 隧道 {TUNNEL_NAME} …")
rc, out = run(["tunnel", "route", "dns", TUNNEL_NAME, hostname])
print("     " + out.replace("\n", "\n     ")[:300])
if rc != 0:
    print("     ⚠ 绑定可能失败（域名没在 Cloudflare 托管时常见）。可在 Cloudflare 后台手动加 CNAME：")
    print(f"       {hostname}  →  {tunnel_id}.cfargotunnel.com")

print("\n[4/4] 完成！接下来这样启动并使用：")
print("  · 启动固定域名隧道： scripts\\start_cloudflare_named.bat")
print("  · 开机自启（可选）：  scripts\\install_autostart.bat")
print(f"  · 状态/二维码：       python scripts\\tunnel_status.py")
print(f"  · 外网地址：          https://{hostname}")
print(f"\n  详细说明见 {DOC} 路线 A2。")
print("═" * 62)
