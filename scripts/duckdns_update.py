# -*- coding: utf-8 -*-
"""固定地址（零成本方案）：用免费的 DuckDNS 二级域名 + 自动跟随本机公网 IP 变化

为什么需要它：家用/公司宽带的公网 IP 会变（几天到几周一次）。
本脚本定时把"当前公网 IP"报到 DuckDNS，于是你有一个**永远不变的域名**，
例如 qms-abc123.duckdns.org，配上路由器端口映射后，外网就能长期访问。

用法：
  python scripts\\duckdns_update.py --setup qms-abc123 <token>   一次性配置（subdomain/token 在 duckdns.org 注册后可看到）
  python scripts\\duckdns_update.py                             立即更新一次并显示结果
  python scripts\\duckdns_update.py --daemon                    常驻，每 5 分钟自动更新（开机自启用）
  python scripts\\duckdns_update.py --status                    查看配置与当前公网 IP

前置（人工 2 步，各 1 分钟）：
  1) 到 https://www.duckdns.org 用微信/GitHub/Google 登录，建一个子域名（如 qms-abc123），复制 token
  2) 在路由器上做端口映射：外部端口 18080 → 本机 192.168.100.197:8000（TCP）
     并在本机以管理员运行 scripts\\open_firewall_public.bat 放行 18080
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.parse
import urllib.request

CFG_DIR = os.path.join(os.path.expanduser("~"), ".qms")
CFG = os.path.join(CFG_DIR, "duckdns.json")
LOG = os.path.join(CFG_DIR, "duckdns.log")
API = "https://www.duckdns.org/update"
PORT_HINT = 18080


def log(msg):
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line)
    try:
        os.makedirs(CFG_DIR, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load_cfg():
    if os.path.exists(CFG):
        try:
            return json.load(open(CFG, encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cfg(d):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(CFG, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    log(f"配置已保存：{CFG}")


def public_ip():
    for u in ("https://ip.3322.net", "https://api.ipify.org", "http://ip-api.com/line/?fields=query"):
        try:
            with urllib.request.urlopen(u, timeout=12) as r:
                ip = r.read().decode().strip().splitlines()[0].strip()
            if ip and ip.count(".") == 3:
                return ip
        except Exception:
            continue
    return ""


def update(sub, token, ip=""):
    """向 DuckDNS 上报当前 IP（ip 为空则由 DuckDNS 取对端 IP）"""
    q = f"?domains={urllib.parse.quote(sub)}&token={urllib.parse.quote(token)}&verbose=true"
    if ip:
        q += f"&ip={urllib.parse.quote(ip)}"
    try:
        with urllib.request.urlopen(API + q, timeout=20) as r:
            txt = r.read().decode().strip()
    except Exception as e:
        return False, f"请求失败：{str(e)[:90]}"
    return ("OK" in txt.upper()), txt.replace("\n", " | ")[:160]


def host_url(sub):
    return f"http://{sub}.duckdns.org:{PORT_HINT}"


def main():
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("--setup", nargs=2, metavar=("SUBDOMAIN", "TOKEN"), help="一次性配置")
    ap.add_argument("--daemon", action="store_true", help="常驻，每 5 分钟更新")
    ap.add_argument("--status", action="store_true", help="查看配置与当前公网 IP")
    ap.add_argument("--interval", type=int, default=300, help="更新间隔秒（默认 300）")
    args = ap.parse_args()

    if args.setup:
        sub, token = args.setup
        sub = sub.replace(".duckdns.org", "").strip()
        save_cfg({"subdomain": sub, "token": token.strip()})
        ip = public_ip()
        log(f"本机公网 IP：{ip or '未取到'}")
        ok, msg = update(sub, token, ip)
        log(("✔ 更新成功：" if ok else "✘ 更新失败：") + msg)
        log(f"外网固定地址（配好端口映射后）：{host_url(sub)}")
        log(f"下一步：① 管理员运行 scripts\\open_firewall_public.bat  ② 路由器把外部 "
            f"{PORT_HINT} 映射到本机 8000")
        return 0 if ok else 1

    cfg = load_cfg()
    sub, token = cfg.get("subdomain", ""), cfg.get("token", "")

    if args.status or not sub:
        ip = public_ip()
        print("─" * 56)
        print("DuckDNS 固定地址状态")
        print("─" * 56)
        print(f"  子域名   : {sub or '（未配置）'}")
        print(f"  token    : {'已配置（' + token[:6] + '…）' if token else '（未配置）'}")
        print(f"  本机公网IP: {ip or '未取到'}")
        print(f"  固定地址 : {host_url(sub) if sub else '（未配置）'}")
        print(f"  配置/日志 : {CFG}\n            {LOG}")
        if not sub:
            print("\n首次配置：python scripts\\duckdns_update.py --setup 你的子域名 你的token")
        return 0

    if args.daemon:
        log(f"启动守护：每 {args.interval} 秒更新一次 {sub}.duckdns.org（Ctrl+C 结束）")
        while True:
            ip = public_ip()
            ok, msg = update(sub, token, ip)
            log(("✔ " if ok else "✘ ") + f"{sub}.duckdns.org ← {ip or '?'}  {msg}")
            time.sleep(max(60, args.interval))

    ip = public_ip()
    log(f"本机公网 IP：{ip or '未取到'}")
    ok, msg = update(sub, token, ip)
    log(("✔ 固定地址已指向本机：" if ok else "✘ 更新失败：") + msg)
    log(f"外网固定地址：{host_url(sub)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
