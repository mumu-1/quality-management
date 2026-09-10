# -*- coding: utf-8 -*-
"""数据库备份 / 恢复 / 保留策略（第6步运维）
用法：
  python scripts/backup.py backup                 # 备份（默认到 backups/，保留 30 天）
  python scripts/backup.py list                   # 列出备份
  python scripts/backup.py restore <备份文件>      # 恢复（会先把当前库再备份一次）
  python scripts/backup.py verify <备份文件>       # 校验备份可读（表数/关键行数）

说明：
- 开发库(SQLite)：使用 sqlite3 在线备份 API，服务运行中也能得到一致的快照。
- 生产库(MySQL)：环境变量 QMS_DB_URL 指向 MySQL 时，改用 mysqldump（需本机有 mysqldump）。
- 建议服务器上用 Windows 计划任务/定时任务每天跑一次 `backup`（保留 30 天自动清理）。
"""
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_DB = os.path.join(BASE_DIR, "qms.db")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
KEEP_DAYS = 30


def _db_url():
    return os.environ.get("QMS_DB_URL", "").strip()


def _is_mysql():
    return _db_url().startswith("mysql")


def cmd_backup(db_path=None, bdir=BACKUP_DIR, keep_days=KEEP_DAYS, quiet=False):
    os.makedirs(bdir, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    if _is_mysql():
        # mysqldump 路径：从 URL 解析账户信息
        url = _db_url()
        # mysql+pymysql://user:pass@host:port/dbname
        try:
            body = url.split("://", 1)[1]
            cred, hostpart = body.split("@", 1)
            user, pwd = (cred.split(":", 1) + [""])[:2]
            hostport, dbname = hostpart.split("/", 1)
            host, port = (hostport.split(":", 1) + ["3306"])[:2]
        except Exception as e:
            raise SystemExit(f"无法解析 QMS_DB_URL: {e}")
        out = os.path.join(bdir, f"qms_mysql_{ts}.sql")
        cmd = ["mysqldump", "-h", host, "-P", str(port), "-u", user,
               f"-p{pwd}", "--single-transaction", "--default-character-set=utf8mb4", dbname]
        with open(out, "wb") as f:
            r = subprocess.run(cmd, stdout=f, stderr=subprocess.PIPE)
        if r.returncode != 0:
            raise SystemExit("mysqldump 失败：" + r.stderr.decode("utf-8", "ignore")[:300])
        size = os.path.getsize(out)
        if not quiet:
            print(f"✔ MySQL 备份完成: {os.path.basename(out)} ({size/1024:.1f} KB)")
    else:
        src = db_path or DEFAULT_DB
        if not os.path.exists(src):
            raise SystemExit(f"数据库文件不存在: {src}")
        out = os.path.join(bdir, f"qms_{ts}.db")
        # 在线备份 API：服务运行中也能拿到一致快照
        s = sqlite3.connect(src)
        d = sqlite3.connect(out)
        with d:
            s.backup(d)
        d.close()
        s.close()
        size = os.path.getsize(out)
        if not quiet:
            print(f"✔ SQLite 备份完成: {os.path.basename(out)} ({size/1024:.1f} KB)")
    # 保留策略：清理超过 keep_days 的备份
    cutoff = datetime.now() - timedelta(days=keep_days)
    removed = 0
    for f in os.listdir(bdir):
        if not (f.startswith("qms_") and (f.endswith(".db") or f.endswith(".sql"))):
            continue
        p = os.path.join(bdir, f)
        mtime = datetime.fromtimestamp(os.path.getmtime(p))
        if mtime < cutoff:
            os.remove(p)
            removed += 1
    kept = len([f for f in os.listdir(bdir) if f.startswith("qms_")])
    if not quiet:
        print(f"  保留策略: 清理 {removed} 个超期备份，当前保留 {kept} 个（上限 {keep_days} 天）")
    return out


def cmd_list(bdir=BACKUP_DIR):
    if not os.path.isdir(bdir):
        print("（暂无备份目录）")
        return []
    rows = []
    for f in sorted(os.listdir(bdir), reverse=True):
        if f.startswith("qms_"):
            p = os.path.join(bdir, f)
            st = os.stat(p)
            rows.append((f, st.st_size, datetime.fromtimestamp(st.st_mtime)))
    if not rows:
        print("（暂无备份）")
    for f, size, mt in rows:
        print(f"  {f}  {size/1024:>8.1f} KB  {mt.strftime('%Y-%m-%d %H:%M:%S')}")
    return rows


def _sqlite_path():
    if _is_mysql():
        raise SystemExit("当前配置为 MySQL，恢复请手工执行: mysql -u用户 -p 库名 < 备份.sql")
    return DEFAULT_DB


def cmd_verify(backup_file, quiet=False):
    """校验备份可读：列出表数量与几张关键表的行数"""
    if not os.path.exists(backup_file):
        raise SystemExit(f"备份文件不存在: {backup_file}")
    if backup_file.endswith(".sql"):
        print("（MySQL .sql 备份：请用 grep 'CREATE TABLE' 粗查，或恢复到临时库验证）")
        return True
    c = sqlite3.connect(backup_file)
    cur = c.cursor()
    tables = [r[0] for r in cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    stats = {}
    for t in ("user", "material", "qc_standard", "incoming_lot", "production_lot", "test_record", "coa"):
        if t in tables:
            try:
                stats[t] = cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.Error:
                stats[t] = "?"
    c.close()
    if not quiet:
        print(f"✔ 备份可读: {os.path.basename(backup_file)}")
        print(f"  表数量: {len(tables)}")
        print("  关键表行数: " + " ".join(f"{k}={v}" for k, v in stats.items()))
    return True


def cmd_restore(backup_file, db_path=None, quiet=False):
    """恢复：先把当前库另存为一份（防误操作），再用备份覆盖"""
    if not os.path.exists(backup_file):
        raise SystemExit(f"备份文件不存在: {backup_file}")
    target = db_path or _sqlite_path()
    if os.path.exists(target):
        # 恢复前快照：放在备份文件所在目录（便于和本次恢复配套管理）
        pre_dir = os.path.dirname(os.path.abspath(backup_file)) or BACKUP_DIR
        os.makedirs(pre_dir, exist_ok=True)
        pre = os.path.join(pre_dir, "qms_before_restore_"
                           + datetime.now().strftime("%Y%m%d_%H%M%S") + ".db")
        shutil.copy2(target, pre)
        if not quiet:
            print(f"  已保存恢复前快照: {os.path.basename(pre)}")
    shutil.copy2(backup_file, target)
    if not quiet:
        print(f"✔ 已恢复: {os.path.basename(backup_file)} → {os.path.basename(target)}")
        cmd_verify(target, quiet=False)
    return target


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return
    cmd = args[0]
    if cmd == "backup":
        cmd_backup()
    elif cmd == "list":
        cmd_list()
    elif cmd == "verify":
        if len(args) < 2:
            raise SystemExit("用法: python scripts/backup.py verify <备份文件>")
        cmd_verify(args[1])
    elif cmd == "restore":
        if len(args) < 2:
            raise SystemExit("用法: python scripts/backup.py restore <备份文件>")
        cmd_restore(args[1])
    else:
        raise SystemExit(f"未知命令: {cmd}\n" + __doc__)


if __name__ == "__main__":
    main()
