# -*- coding: utf-8 -*-
"""并发压测（第6步验收：200 并发，响应 < 2 秒）
用法：
  python scripts/load_test.py --users 200 --seconds 20
  python scripts/load_test.py --users 50 --seconds 10 --base http://192.168.1.10:8000

说明：
- 混合负载：约 70% 读（列表/追溯/报表/大屏）+ 30% 登录（含写 token 表，验证写路径）
- 输出：总请求、成功率、平均/P95/最大响应时间、QPS，并给出"是否达标"结论
- 本机 SQLite 单进程是开发基线；上线应指向服务器（MySQL + 多 worker）再测一次
"""
import argparse
import json
import statistics
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

READ_PATHS = ["/api/production-lots", "/api/incoming", "/api/ncr",
              "/api/reports/summary?days=30", "/api/screen", "/api/coas"]
CREDS = [("admin", "123456"), ("qc", "123456"), ("qm", "123456")]


class Stats:
    def __init__(self):
        self.lat = []
        self.ok = 0
        self.fail = 0
        self.codes = {}
        self.lock = threading.Lock()

    def add(self, ms, code):
        with self.lock:
            self.lat.append(ms)
            if 200 <= code < 300:
                self.ok += 1
            else:
                self.fail += 1
            self.codes[code] = self.codes.get(code, 0) + 1


def http(method, url, body=None, token=None, timeout=30):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("token", token)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return (time.perf_counter() - t0) * 1000, r.status
    except urllib.error.HTTPError as e:
        e.read()
        return (time.perf_counter() - t0) * 1000, e.code
    except Exception:      # noqa: BLE001
        return (time.perf_counter() - t0) * 1000, 0


def worker(base, token, stats, deadline, readonly=False):
    i = 0
    while time.time() < deadline:
        i += 1
        if (not readonly) and i % 10 == 0:
            u, p = CREDS[i % len(CREDS)]
            ms, code = http("POST", base + "/api/auth/login", {"username": u, "password": p})
        else:
            path = READ_PATHS[i % len(READ_PATHS)]
            ms, code = http("GET", base + path, token=token)
        stats.add(ms, code)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--users", type=int, default=200)
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--readonly", action="store_true",
                    help="纯读压测（不含登录写操作），用于定位写锁瓶颈")
    a = ap.parse_args()
    base = a.base.rstrip("/")

    # 预热 + 取 token
    ms, code = http("POST", base + "/api/auth/login", {"username": "admin", "password": "123456"})
    tok_req = urllib.request.Request(base + "/api/auth/login",
                                     data=json.dumps({"username": "admin", "password": "123456"}).encode(),
                                     method="POST")
    tok_req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(tok_req, timeout=30) as r:
        token = json.loads(r.read().decode())["token"]

    print(f"压测目标：{base}")
    print(f"并发用户：{a.users}　持续：{a.seconds}s　负载：" +
          ("纯读（无写）" if a.readonly else "约 70% 读 + 30% 登录(写)"))
    stats = Stats()
    deadline = time.time() + a.seconds
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=a.users) as ex:
        for _ in range(a.users):
            ex.submit(worker, base, token, stats, deadline, a.readonly)
    dur = time.time() - t0

    lat = sorted(stats.lat)
    n = len(lat)
    if not n:
        print("✘ 无请求完成，请检查服务是否启动")
        sys.exit(1)
    avg = statistics.mean(lat)
    p95 = lat[int(n * 0.95) - 1]
    p99 = lat[int(n * 0.99) - 1] if n >= 100 else lat[-1]
    mx = lat[-1]
    ok_rate = stats.ok * 100.0 / n
    qps = n / dur
    print("\n──── 压测结果 ────")
    print(f"总请求 {n}　成功 {stats.ok}　失败 {stats.fail}　成功率 {ok_rate:.2f}%")
    print(f"QPS {qps:.1f}　平均 {avg:.0f} ms　P95 {p95:.0f} ms　P99 {p99:.0f} ms　最大 {mx:.0f} ms")
    print("状态码分布: " + "  ".join(f"{k}×{v}" for k, v in sorted(stats.codes.items())))
    print("\n──── 判定（验收标准：响应 < 2 秒）────")
    verdict = []
    verdict.append(("成功率 ≥ 99%", ok_rate >= 99))
    verdict.append(("P95 响应 < 2000 ms", p95 < 2000))
    verdict.append(("平均响应 < 1000 ms", avg < 1000))
    for name, ok in verdict:
        print(("  ✔ " if ok else "  ✘ ") + name)
    allok = all(v for _, v in verdict)
    print("\n" + ("✔ 压测通过" if allok else "✘ 未达标（本机 SQLite 单进程属开发基线；请在服务器 MySQL + 多 worker 环境复测）"))
    # 输出机器可读结论，便于 e2e 断言
    print(json.dumps({"qps": round(qps, 1), "avg_ms": round(avg), "p95_ms": round(p95),
                      "ok_rate": round(ok_rate, 2), "passed": allok, "requests": n},
                     ensure_ascii=False))
    sys.exit(0 if allok else 2)


if __name__ == "__main__":
    main()
