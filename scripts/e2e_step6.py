# -*- coding: utf-8 -*-
"""第6步 e2e 验收：上线部署能力
覆盖：
 1) 备份 / 恢复演练（真跑：备份→改库→恢复→数据回到备份时点）
 2) 批量账号创建（含"初始密码表里的密码能真的登录"验证）
 3) SQLite→MySQL 迁移工具（干跑生成 SQL / 同库自比校验）
 4) 并发压测（50 并发小跑，验证脚本可用与成功率）
 5) 局域网访问能力（监听 0.0.0.0，可用本机 IP 访问健康检查）
 6) 交付文档齐备（运维手册 / 各角色操作手册 / 使用说明）
 7) 新角色"操作工"权限最小化
运行: python scripts/e2e_step6.py （先启动服务）
注意：本脚本会写少量测试数据（账号/物料）并做备份恢复演练；跑完请重置演示库。
"""
import io
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import urllib.error

BASE = "http://localhost:8000"
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(PROJ, "scripts")
sys.path.insert(0, SCRIPTS)
sys.path.insert(0, PROJ)

PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


def call(method, path, body=None, token="", raw=False):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("token", token)
    try:
        with urllib.request.urlopen(req) as r:
            d = r.read()
            return d if raw else json.loads(d.decode())
    except urllib.error.HTTPError as e:
        return ({"_err": e.code, "_detail": e.read().decode()[:200]} if not raw else b"")


def login(u, p="123456"):
    return call("POST", "/api/auth/login", {"username": u, "password": p})


AT = login("admin").get("token", "")
check("管理员登录", bool(AT))

print("══ 1. 备份 / 恢复演练（真跑）══")
import backup as bk  # noqa: E402

drill_dir = tempfile.mkdtemp(prefix="qms_drill_")
drill_db = os.path.join(drill_dir, "prod.db")
drill_bdir = os.path.join(drill_dir, "backups")
shutil.copy2(os.path.join(PROJ, "qms.db"), drill_db)

# 1a. 备份
bk_file = bk.cmd_backup(db_path=drill_db, bdir=drill_bdir, keep_days=30, quiet=True)
check("备份文件已生成", os.path.exists(bk_file) and os.path.getsize(bk_file) > 10000,
      f"{os.path.getsize(bk_file)/1024:.0f} KB")
check("备份可读且含关键表", bk.cmd_verify(bk_file, quiet=True) is True)
lst = bk.cmd_list(drill_bdir)
check("备份列表可查询", len(lst) >= 1)

# 1b. 模拟"数据被误改"
con = sqlite3.connect(drill_db)
con.execute("INSERT INTO material (code, name, material_type, spec, unit, enabled, created_at) "
            "VALUES ('DRILL-001','演练后新增物料','原料','x','t',1,datetime('now'))")
con.commit()
n_after = con.execute("SELECT COUNT(*) FROM material WHERE code='DRILL-001'").fetchone()[0]
con.close()
check("演练库已改动(新增 1 条)", n_after == 1)

# 1c. 恢复
bk.cmd_restore(bk_file, db_path=drill_db, quiet=True)
con = sqlite3.connect(drill_db)
n_restored = con.execute("SELECT COUNT(*) FROM material WHERE code='DRILL-001'").fetchone()[0]
total_restored = con.execute("SELECT COUNT(*) FROM material").fetchone()[0]
con.close()
check("★ 恢复后数据回到备份时点(新增物料消失)", n_restored == 0, f"现有物料 {total_restored} 条")
pre = [f for f in os.listdir(drill_bdir) if f.startswith("qms_before_restore")]
check("恢复前自动留存快照", len(pre) >= 1, pre[0] if pre else "")
# 1d. 保留策略：造一个 40 天前的旧备份，再备份一次应被清理
old = os.path.join(drill_bdir, "qms_20000101_000000.db")
shutil.copy2(bk_file, old)
os.utime(old, (time.time() - 40 * 86400, time.time() - 40 * 86400))
bk.cmd_backup(db_path=drill_db, bdir=drill_bdir, keep_days=30, quiet=True)
check("超 30 天旧备份被自动清理", not os.path.exists(old))
# 1e. 无备份文件时恢复应报错退出
try:
    bk.cmd_restore(os.path.join(drill_bdir, "不存在.db"), db_path=drill_db, quiet=True)
    check("缺失备份文件时报错", False, "未报错")
except SystemExit:
    check("缺失备份文件时报错", True)
shutil.rmtree(drill_dir, ignore_errors=True)

print("══ 2. 批量账号创建（含密码可登录验证）══")
import csv as _csv  # noqa: E402

ts = str(int(time.time()) % 100000)
U_QC, U_WK = f"qc_t{ts}a", f"worker_t{ts}b"
csv_path = os.path.join(tempfile.gettempdir(), f"qms_test_users_{ts}.csv")
with io.open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
    w = _csv.writer(f)
    w.writerow(["姓名", "部门", "岗位", "工序编码", "用户名"])
    w.writerow(["测试检验", "质量部", "检验员", "ST03", U_QC])
    w.writerow(["测试工人", "生产部", "操作工", "ST01", U_WK])
r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "batch_users.py"),
                    "csv", csv_path, "--password", "Test123456"],
                   capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
check("批量账号脚本执行成功(CSV 名单)", r.returncode == 0,
      (r.stdout or "").strip().splitlines()[-1][:80])
created_ok = bool(login(U_QC, "Test123456").get("token")) and bool(login(U_WK, "Test123456").get("token"))
check("★ 初始密码表里的密码可真实登录", created_ok, f"新账号 {U_QC} / {U_WK}")
pwd_tables = [f for f in os.listdir(PROJ) if f.startswith("账号初始密码表_")]
check("初始密码表已导出", len(pwd_tables) >= 1, pwd_tables[0] if pwd_tables else "")
dry = subprocess.run([sys.executable, os.path.join(SCRIPTS, "batch_users.py"), "demo", "5", "--dry-run"],
                     capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
check("试运行模式可用且不写库", dry.returncode == 0 and "试运行" in (dry.stdout or ""))

print("══ 3. SQLite→MySQL 迁移工具 ══")
sql_out = os.path.join(tempfile.gettempdir(), "qms_migrate_test.sql")
r2 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "migrate_to_mysql.py"),
                     "dryrun", "--out", sql_out],
                    capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
check("干跑生成迁移 SQL", r2.returncode == 0 and os.path.exists(sql_out),
      f"{os.path.getsize(sql_out)/1024:.0f} KB" if os.path.exists(sql_out) else r2.stderr[:100])
sql_text = io.open(sql_out, encoding="utf-8").read() if os.path.exists(sql_out) else ""
check("SQL 含 MySQL 建表语句", "CREATE TABLE IF NOT EXISTS" in sql_text and "AUTO_INCREMENT" in sql_text)
check("SQL 含数据插入语句", "INSERT INTO " in sql_text)
check("SQL 含外键检查开关(迁移安全)", "SET FOREIGN_KEY_CHECKS=0" in sql_text and "FOREIGN_KEY_CHECKS=1" in sql_text)
os.remove(sql_out) if os.path.exists(sql_out) else None
# 同库自比（源=目标，验证校验逻辑本身可用）
import migrate_to_mysql as mg  # noqa: E402

try:
    mg.verify(os.path.join(PROJ, "qms.db"), "sqlite:///" + os.path.join(PROJ, "qms.db").replace("\\", "/"))
    check("校验逻辑可用(同库自比一致)", True)
except SystemExit as e:
    check("校验逻辑可用(同库自比一致)", False, str(e)[:80])

print("══ 4. 并发压测（小规模验证脚本可用）══")
r3 = subprocess.run([sys.executable, os.path.join(SCRIPTS, "load_test.py"),
                     "--users", "50", "--seconds", "8", "--readonly"],
                    capture_output=True, text=True, encoding="utf-8", cwd=PROJ)
out = r3.stdout or ""
metric = {}
for line in out.splitlines():
    if line.strip().startswith('{"qps"'):
        try:
            metric = json.loads(line)
        except Exception:  # noqa: BLE001
            pass
check("压测脚本可运行并产出指标", bool(metric), json.dumps(metric, ensure_ascii=False))
check("压测成功率 ≥ 99%", metric.get("ok_rate", 0) >= 99, f"{metric.get('ok_rate')}%")
check("50 并发平均响应 < 3 秒（本机弱机基线）", metric.get("avg_ms", 99999) < 3000,
      f"{metric.get('avg_ms')} ms")

print("══ 5. 局域网访问能力 ══")
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    lan_ip = s.getsockname()[0]
    s.close()
except Exception:      # noqa: BLE001
    lan_ip = ""
check("取得本机局域网 IP", bool(lan_ip), lan_ip)
if lan_ip:
    try:
        with urllib.request.urlopen(f"http://{lan_ip}:8000/api/health", timeout=8) as r:
            d = json.loads(r.read().decode())
        check("★ 局域网地址可访问(车间电脑/手机同网即可)", d.get("ok") is True)
    except Exception as e:      # noqa: BLE001
        check("★ 局域网地址可访问", False, str(e)[:80])
page = call("GET", "/", raw=True)
ps = page.decode("utf-8", "ignore") if isinstance(page, bytes) else str(page)
check("首页可访问且含全部模块", all(k in ps for k in ["renderScreenPage", "renderTracePage", "renderReportPage"]))

print("══ 6. 交付文档 ══")
for fn, kw in [("交付文档-运维手册.md", "上线检查清单"),
               ("交付文档-各角色操作手册.md", "检验员"),
               ("使用说明-通俗版.md", "批次追溯"),
               ("agents.md", "commit")]:
    p = os.path.join(PROJ, fn)
    ok = os.path.exists(p) and kw in io.open(p, encoding="utf-8").read()
    check(f"文档齐备: {fn}", ok)

print("══ 7. 操作工角色权限最小化 ══")
# 刚批量创建的操作工账号（最小权限验证）
wk = login(U_WK, "Test123456")
if wk.get("token"):
    menus = sorted(m["key"] for m in wk.get("menus", []))
    check("操作工只见 2 个页面", menus == ["dashboard", "prodlot"], str(menus))
    check("操作工不能建生产批",
          call("POST", "/api/production-lots", {"station_id": 1, "parent_lot_no": "x", "qty": 1},
               token=wk["token"]).get("_err") == 403)
    check("操作工不能看报表", call("GET", "/api/reports/summary", token=wk["token"]).get("_err") == 403)
else:
    check("操作工账号可登录", False, str(wk))

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第6步验收全部通过")
