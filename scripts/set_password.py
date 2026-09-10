# -*- coding: utf-8 -*-
"""账号口令治理：查看弱口令风险 / 改密 / 一键强化 / 停用演示账号
用法：
  python scripts/set_password.py list                 查看所有启用账号与弱口令风险
  python scripts/set_password.py set <账号> <新密码>     给某账号设置新密码（≥8位，含字母+数字）
  python scripts/set_password.py harden               给全部启用账号生成随机强密码并导出 CSV
  python scripts/set_password.py disable-demo         停用演示账号（worker/sampler/store/buyer…保留 admin/qm/qc）
  python scripts/set_password.py enable <账号>          重新启用某账号
说明：与系统同一套密码算法（PBKDF2-SHA256，10万次迭代），改完立即生效。
"""
import csv
import hashlib
import os
import secrets
import string
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import SessionLocal          # noqa: E402
import models as M                          # noqa: E402

WEAK = {"123456", "12345678", "123456789", "111111", "000000", "666666", "888888",
        "password", "admin", "admin123", "abc123", "qwerty", "123123", "88888888",
        "qms123456", "1234", "admin888"}
DEMO_KEEP = {"admin", "qm", "qc"}


def hash_pwd(pwd, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 100_000).hex()
    return salt, h


def strong_pwd(n=12):
    alphabet = string.ascii_letters + string.digits
    while True:
        p = "".join(secrets.choice(alphabet) for _ in range(n))
        if any(c.isdigit() for c in p) and any(c.isalpha() for c in p):
            return p


def is_weak(pwd):
    return pwd.lower() in WEAK or len(pwd) < 8


def check_strength(pwd):
    if len(pwd) < 8:
        return False, "至少 8 位"
    if not any(c.isdigit() for c in pwd):
        return False, "要含数字"
    if not any(c.isalpha() for c in pwd):
        return False, "要含字母"
    if pwd.lower() in WEAK:
        return False, "是常见弱口令"
    return True, ""


def main():
    args = sys.argv[1:]
    db = SessionLocal()
    try:
        cmd = args[0] if args else "list"

        if cmd == "list":
            users = db.query(M.User).order_by(M.User.id).all()
            print(f"{'账号':<14}{'姓名':<10}{'角色':<10}{'部门/职务':<22}{'状态':<6}")
            print("-" * 70)
            for u in users:
                dep = db.get(M.Department, u.dept_id) if u.dept_id else None
                pos = db.get(M.Position, u.position_id) if u.position_id else None
                dp = f"{dep.name if dep else ''}/{pos.name if pos else ''}"
                print(f"{u.username:<14}{u.real_name or '':<10}{u.role_key:<10}{dp:<22}"
                      f"{'启用' if u.enabled else '停用':<6}")
            print()
            print("⚠️ 演示口令若仍是 123456，务必在公网暴露前执行：")
            print("   python scripts/set_password.py harden     （全部账号随机强密码并导出 CSV）")
            print("   或 python scripts/set_password.py set admin 你的新密码")
            print("   python scripts/set_password.py disable-demo  （停用不需要的演示账号）")
            return 0

        if cmd == "set":
            if len(args) < 3:
                print("用法：python scripts/set_password.py set <账号> <新密码>")
                return 2
            uname, newp = args[1], args[2]
            ok, why = check_strength(newp)
            if not ok:
                print(f"✘ 新密码不合格：{why}")
                return 2
            u = db.query(M.User).filter(M.User.username == uname).first()
            if not u:
                print(f"✘ 账号不存在：{uname}")
                return 2
            u.password_salt, u.password_hash = hash_pwd(newp)
            # 改密后作废该账号所有已登录 token（安全要求）
            db.query(M.AuthToken).filter(M.AuthToken.user_id == u.id).delete()
            db.commit()
            print(f"✔ 已设置 {uname} 的新密码（该账号原登录已全部失效，需重新登录）")
            return 0

        if cmd == "harden":
            users = db.query(M.User).filter(M.User.enabled == True).all()  # noqa: E712
            rows = []
            for u in users:
                p = strong_pwd()
                u.password_salt, u.password_hash = hash_pwd(p)
                db.query(M.AuthToken).filter(M.AuthToken.user_id == u.id).delete()
                rows.append([u.username, u.real_name or "", u.role_key, p])
            db.commit()
            fn = f"账号新密码表_{__import__('datetime').datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
            with open(fn, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow(["账号", "姓名", "角色", "新密码"])
                w.writerows(rows)
            print(f"✔ 已为 {len(rows)} 个启用账号重置为随机强密码")
            print(f"✔ 密码表已导出：{fn}  ← 请妥善保存并尽快删除（不要提交到代码库）")
            print("  提示：所有账号原登录已失效，需用新密码重新登录。")
            return 0

        if cmd == "disable-demo":
            n = 0
            for u in db.query(M.User).all():
                if u.username in DEMO_KEEP:
                    continue
                if u.username in ("sampler", "store", "buyer", "prodlead", "worker", "boss",
                                  "qc2", "opuser", "demo"):
                    u.enabled = False
                    db.query(M.AuthToken).filter(M.AuthToken.user_id == u.id).delete()
                    n += 1
            db.commit()
            print(f"✔ 已停用 {n} 个演示账号（保留 {'/'.join(sorted(DEMO_KEEP))}）")
            return 0

        if cmd == "enable":
            if len(args) < 2:
                print("用法：python scripts/set_password.py enable <账号>")
                return 2
            u = db.query(M.User).filter(M.User.username == args[1]).first()
            if not u:
                print(f"✘ 账号不存在：{args[1]}")
                return 2
            u.enabled = True
            db.commit()
            print(f"✔ 已启用账号 {u.username}")
            return 0

        print(__doc__)
        return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
