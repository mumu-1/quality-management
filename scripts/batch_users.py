# -*- coding: utf-8 -*-
"""批量创建账号 + 导出初始密码表（第6步上线用）
用法：
  python scripts/batch_users.py demo 1500            # 演示：生成 1500 个账号（按部门/角色分布）
  python scripts/batch_users.py csv 名单.csv          # 从名单导入（列：姓名,部门,岗位,工序编码,用户名可选）
  python scripts/batch_users.py demo 200 --password 123456   # 统一初始密码
  python scripts/batch_users.py demo 50 --dry-run     # 只预览不写库

输出：
  - 账号写入数据库（角色默认模板即权限，各账号权限可在"账号管理"里单独调整）
  - 初始密码表 CSV：账号初始密码表_YYYYMMDD_HHMMSS.csv（含明文初始密码，发放后请删除）
幂等：已存在的用户名自动跳过。
"""
import csv
import hashlib
import os
import secrets
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal, ensure_schema        # noqa: E402
import models as M                                       # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 岗位名 → 角色 key（CSV 导入时用）
POST_TO_ROLE = {
    "管理员": "admin", "系统管理员": "admin",
    "总经理": "boss", "高层": "boss", "副总": "boss",
    "质量经理": "qm", "质量主管": "qm",
    "检验员": "qc", "质检员": "qc", "化验员": "qc",
    "取样员": "sampler",
    "班组长": "prodlead", "班长": "prodlead", "车间主管": "prodlead", "主任": "prodlead",
    "采购": "buyer", "采购员": "buyer",
    "仓管": "store", "仓储": "store", "库管": "store",
    "操作工": "worker", "工人": "worker", "操作员": "worker",
}

# demo 模式的部门/角色分布（合计 1500）
DEMO_PLAN = [
    ("信息部", "系统管理员", "admin", 2),
    ("总经办", "高层", "boss", 3),
    ("质量部", "质量经理", "qm", 12),
    ("质量部", "检验员", "qc", 180),
    ("质量部", "取样员", "sampler", 70),
    ("生产部", "班组长", "prodlead", 160),
    ("生产部", "操作工", "worker", 1000),
    ("采购部", "采购", "buyer", 25),
    ("仓储部", "仓管", "store", 48),
]


def hash_pwd(pwd, salt=None):
    """与 main.py 完全一致的密码算法（PBKDF2-SHA256, 100k 迭代）"""
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 100_000).hex()
    return salt, h


def gen_password():
    return secrets.token_urlsafe(9)[:10]


def load_csv_plan(path):
    """从名单 CSV 读取（自动识别表头；工序编码可空）"""
    rows = []
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.reader(f)
        header = next(rd, None)
        for r in rd:
            if not r or not any(x.strip() for x in r):
                continue
            r = (r + [""] * 5)[:5]
            name, dept, post, stn, uname = [x.strip() for x in r]
            role = POST_TO_ROLE.get(post, "worker")
            rows.append({"name": name, "dept": dept, "post": post or post,
                         "stn": stn, "uname": uname, "role": role})
    return rows


def make_demo_plan(total):
    """按 DEMO_PLAN 比例生成 total 个账号"""
    base = sum(x[3] for x in DEMO_PLAN)
    scale = total / base
    plan, used = [], 0
    for i, (dept, post, role, n) in enumerate(DEMO_PLAN):
        cnt = int(round(n * scale))
        if i == len(DEMO_PLAN) - 1:
            cnt = max(total - used, 0)
        used += cnt
        plan.append((dept, post, role, cnt))
    return plan


def run(plan_rows, db, password=None, dry_run=False):
    st_map = {s.code: s for s in db.query(M.Station).all()}
    created, skipped, out_rows = 0, 0, []
    seq_by_role = {}
    for r in plan_rows:
        role = r["role"]
        seq_by_role[role] = seq_by_role.get(role, 0) + 1
        uname = r.get("uname") or f"{role}{seq_by_role[role]:04d}"
        exists = db.query(M.User).filter(M.User.username == uname).first()
        if exists:
            skipped += 1
            continue
        pwd = password or gen_password()
        stn = st_map.get((r.get("stn") or "").strip().upper())
        if not dry_run:
            salt, h = hash_pwd(pwd)
            u = M.User(username=uname, real_name=r["name"], department=r["dept"],
                       role_key=role, station_id=stn.id if stn else None,
                       password_salt=salt, password_hash=h)
            db.add(u)
        out_rows.append([uname, r["name"], r["dept"], r["post"], role,
                         stn.code if stn else "", pwd])
        created += 1
    if not dry_run:
        db.commit()
    return created, skipped, out_rows


def main():
    args = sys.argv[1:]
    if len(args) < 2 or args[0] not in ("demo", "csv"):
        print(__doc__)
        return
    mode, target = args[0], args[1]
    password = None
    dry_run = "--dry-run" in args
    if "--password" in args:
        password = args[args.index("--password") + 1]

    if mode == "demo":
        total = int(target)
        rows = []
        for dept, post, role, n in make_demo_plan(total):
            for i in range(n):
                rows.append({"name": f"{post}{i+1}", "dept": dept, "post": post,
                             "stn": "", "uname": "", "role": role})
        print(f"演示计划：共 {len(rows)} 个账号（" +
              "、".join(f"{p}{n}人" for _, p, _, n in make_demo_plan(total)) + "）")
    else:
        rows = load_csv_plan(target)
        print(f"名单读取：{len(rows)} 条")

    ensure_schema()
    db = SessionLocal()
    try:
        t0 = datetime.now()
        created, skipped, out_rows = run(rows, db, password, dry_run)
        dt = (datetime.now() - t0).total_seconds()
        print(f"{'（试运行，未写库）' if dry_run else '✔ 完成'}：新建 {created} 个，跳过已存在 {skipped} 个，用时 {dt:.1f}s")
        if out_rows and not dry_run:
            out = os.path.join(BASE_DIR, "账号初始密码表_"
                               + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv")
            with open(out, "w", encoding="utf-8-sig", newline="") as f:
                w = csv.writer(f)
                w.writerow(["用户名", "姓名", "部门", "岗位", "角色", "负责工序", "初始密码"])
                w.writerows(out_rows)
            print(f"✔ 初始密码表已导出：{os.path.basename(out)}（共 {len(out_rows)} 行，发放后请删除）")
            print("  提示：员工首次登录后应立即在「账号管理 → 重置密码」改为自己的密码")
    finally:
        db.close()


if __name__ == "__main__":
    main()
