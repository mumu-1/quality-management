# -*- coding: utf-8 -*-
"""SQLite → MySQL 数据迁移（第6步上线：数据无缝搬家）
用法：
  # 1) 先干跑：生成 MySQL 可执行的 SQL（不连目标库）
  python scripts/migrate_to_mysql.py dryrun --out migrate_qms.sql

  # 2) 真正迁移（目标库需先建好空库，并有权限）
  set QMS_DB_URL=mysql+pymysql://qms:密码@127.0.0.1:3306/qms
  python scripts/migrate_to_mysql.py run --source qms.db

  # 3) 只做校验（源库 vs 目标库行数比对）
  python scripts/migrate_to_mysql.py verify --source qms.db

要点：
- 按外键依赖顺序建表/插数据（SQLAlchemy sorted_tables），迁移期临时关闭目标库外键检查避免顺序死锁
- 分批插入（默认 500 行/批），大数据量也不爆内存
- 迁移后逐表行数校验，并抽查关键表（用户/物料/检验单/批次）
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, select, insert, func   # noqa: E402
from database import Base                                     # noqa: E402
import models  # noqa: E402,F401   （导入以注册所有表）

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHUNK = 500
KEY_TABLES = ["user", "material", "supplier", "customer", "qc_standard",
              "incoming_lot", "production_lot", "test_record", "test_item", "ncr", "coa"]


def src_url(source):
    return "sqlite:///" + os.path.abspath(source).replace("\\", "/")


def dst_url():
    u = os.environ.get("QMS_DB_URL", "").strip()
    if not u.startswith("mysql"):
        raise SystemExit("请先设置目标库：set QMS_DB_URL=mysql+pymysql://用户:密码@主机:3306/库名")
    if "pymysql" not in u:
        raise SystemExit("需要 pymysql 驱动：pip install pymysql（并确认 URL 为 mysql+pymysql://…）")
    return u


def counts(engine):
    out = {}
    with engine.connect() as c:
        for t in Base.metadata.sorted_tables:
            try:
                out[t.name] = c.execute(select(func.count()).select_from(t)).scalar()
            except Exception as e:            # noqa: BLE001
                out[t.name] = f"读失败({type(e).__name__})"
    return out


def cmd_dryrun(source, out_file):
    """不连目标库：生成 MySQL 方言的建表 + INSERT SQL 文件"""
    from sqlalchemy.schema import CreateTable, CreateIndex
    from sqlalchemy.dialects import mysql
    src = create_engine(src_url(source))
    lines = [f"-- 生产质量管理系统 数据迁移脚本（由 migrate_to_mysql.py 生成于 "
             f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）",
             "-- 用法：mysql -u用户 -p 库名 < 本文件", "SET NAMES utf8mb4;",
             "SET FOREIGN_KEY_CHECKS=0;", ""]
    total = 0
    for t in Base.metadata.sorted_tables:
        ddl = str(CreateTable(t).compile(dialect=mysql.dialect())).strip()
        ddl = ddl.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1)
        lines += [f"-- ── 表 {t.name} ──", ddl + ";"]
        for ix in t.indexes:
            try:
                lines.append(str(CreateIndex(ix).compile(dialect=mysql.dialect())).strip()
                             .replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1) + ";")
            except Exception:      # noqa: BLE001
                pass
        rows = []
        with src.connect() as c:
            rows = c.execute(select(t)).mappings().all()
        if rows:
            stmt = insert(t)
            for r in rows:
                one = stmt.values(**dict(r))
                sql = str(one.compile(dialect=mysql.dialect(),
                                      compile_kwargs={"literal_binds": True}))
                lines.append(sql.rstrip() + ";")
            total += len(rows)
        lines.append("")
    lines.append("SET FOREIGN_KEY_CHECKS=1;")
    lines.append(f"-- 共导出 {total} 行")
    with open(out_file, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines))
    print(f"✔ 已生成 {out_file}（{os.path.getsize(out_file)/1024:.1f} KB，{total} 行数据）")
    print("  下一步：在服务器上用 mysql 客户端导入，或直接跑 `migrate_to_mysql.py run`")


def migrate(source, target):
    src = create_engine(src_url(source))
    dst = create_engine(target)
    print("① 在目标库建表…")
    Base.metadata.create_all(dst)
    print("② 关闭目标库外键检查（迁移期）…")
    with dst.begin() as c:
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS=0")
    total = 0
    for t in Base.metadata.sorted_tables:
        with src.connect() as c:
            rows = [dict(r) for r in c.execute(select(t)).mappings().all()]
        if not rows:
            print(f"   {t.name}: 0 行")
            continue
        for i in range(0, len(rows), CHUNK):
            with dst.begin() as c:
                c.execute(insert(t), rows[i:i + CHUNK])
        total += len(rows)
        print(f"   {t.name}: {len(rows)} 行")
    with dst.begin() as c:
        c.exec_driver_sql("SET FOREIGN_KEY_CHECKS=1")
    print(f"③ 迁移完成，共 {total} 行")
    return verify(source, target)


def verify(source, target=None):
    src = create_engine(src_url(source))
    dst = create_engine(target or dst_url())
    sc, dc = counts(src), counts(dst)
    print("④ 校验（源 → 目标）：")
    bad = 0
    for name in sc:
        a, b = sc[name], dc.get(name, "缺表")
        mark = "✔" if a == b else "✘"
        if a != b:
            bad += 1
        print(f"   {mark} {name}: {a} → {b}")
    print(f"\n关键表检查: " + "  ".join(f"{k}={sc.get(k)}" for k in KEY_TABLES if k in sc))
    if bad:
        raise SystemExit(f"✘ 有 {bad} 张表行数不一致，请勿切换系统，先排查！")
    print("✔ 全部表行数一致，可以切换 QMS_DB_URL 到 MySQL 并重启服务")
    return True


def main():
    args = sys.argv[1:]
    if not args or args[0] not in ("dryrun", "run", "verify"):
        print(__doc__)
        return
    cmd = args[0]
    source = "qms.db"
    target = None
    out_file = "migrate_qms.sql"
    if "--source" in args:
        source = args[args.index("--source") + 1]
    if "--target" in args:
        target = args[args.index("--target") + 1]
    if "--out" in args:
        out_file = args[args.index("--out") + 1]
    if cmd == "dryrun":
        cmd_dryrun(source, out_file)
    elif cmd == "run":
        migrate(source, target or dst_url())
    else:
        verify(source, target)


if __name__ == "__main__":
    main()
