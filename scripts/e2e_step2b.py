# -*- coding: utf-8 -*-
"""优化验收：质检员多工序 + 账号级动态权限（页面/管理可勾选）
运行: python scripts/e2e_step2b.py  （先启动服务）
"""
import json, sys, time, urllib.request, urllib.error

BASE = "http://localhost:8000"
TS = str(int(time.time()) % 100000)
UNAME = "qcro" + TS   # 每次运行唯一，避免与上次残留冲突


def call(method, path, body=None, token=""):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("token", token)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_detail": e.read().decode()[:200]}


PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


def keys(d):
    return sorted([m["key"] for m in d.get("menus", [])])


AT = call("POST", "/api/auth/login", {"username": "admin", "password": "123456"}).get("token", "")
check("admin 登录", bool(AT))

print("══ 1. 权限矩阵接口 ══")
pm = call("GET", "/api/admin/perm-matrix", token=AT)
check("perm-matrix 返回全部页面", isinstance(pm.get("pages"), list) and len(pm["pages"]) >= 12, f"{len(pm.get('pages',[]))} 页")
check("perm-matrix 含角色默认模板", isinstance(pm.get("roles"), list) and any(r["key"] == "qc" for r in pm["roles"]))
ro = call("GET", "/api/admin/perm-matrix", token=call("POST", "/api/auth/login", {"username": "qc", "password": "123456"}).get("token", ""))
check("非管理员访问矩阵被拒 403", ro.get("_err") == 403)

print("══ 2. 质检员多工序 ══")
qc_login = call("POST", "/api/auth/login", {"username": "qc", "password": "123456"})
qc_st = sorted(qc_login["user"].get("station_ids", []))
check("qc 登录返回 station_ids(2道工序)", len(qc_st) == 2, str(qc_st))
users = call("GET", "/api/admin/users", token=AT)
qc_row = next((u for u in users if u["username"] == "qc"), None)
check("账号列表含 qc 多工序", qc_row and sorted(qc_row["station_ids"]) == qc_st, str(qc_row.get("station_ids") if qc_row else None))
# 给一个"只负责1道工序"的检验员改成2道工序（模拟质检员扩岗）
prodlead2 = next(u for u in users if u["username"] == "prodlead2")
st_all = [s["id"] for s in call("GET", "/api/station", token=AT)]
upd = call("PUT", f"/api/admin/users/{prodlead2['id']}", {"station_ids": [st_all[0], st_all[4]]}, token=AT)
check("编辑账号工序为2道成功", upd.get("ok") is True)
u2 = next(u for u in call("GET", "/api/admin/users", token=AT) if u["username"] == "prodlead2")
check("保存后 station_ids 生效", sorted(u2["station_ids"]) == sorted([st_all[0], st_all[4]]), str(u2["station_ids"]))
# 清空工序
upd3 = call("PUT", f"/api/admin/users/{prodlead2['id']}", {"station_ids": []}, token=AT)
u3 = next(u for u in call("GET", "/api/admin/users", token=AT) if u["username"] == "prodlead2")
check("清空工序后 station_id 同步为空", upd3.get("ok") is True and u3["station_ids"] == [] and u3["station_id"] is None)

print("══ 3. 账号级动态权限（能看/能管）══")
# 3a. 新建一个"只读质检员"：只给 material 看、不给任何管理
nu = call("POST", "/api/admin/users", {"username": UNAME, "real_name": "只读质检",
                                       "department": "质量部", "role_key": "qc",
                                       "view_pages": ["dashboard", "material"],
                                       "manage_modules": [], "password": "123456"}, token=AT)
check("新建自定义权限账号", nu.get("ok") is True, str(nu.get("_detail", "")))
nuid = nu.get("id")
lg = call("POST", "/api/auth/login", {"username": UNAME, "password": "123456"})
check("登录菜单=自定义(2页)", keys(lg) == ["dashboard", "material"], str(keys(lg)))
check("无任何管理权", not any(m["manage"] for m in lg.get("menus", [])))
# 后端强制：只读质检写物料被拒
over = call("POST", "/api/material", {"code": f"RX1-{TS}", "name": "越权"}, token=lg.get("token", ""))
check("自定义只读 → 写物料 403", over.get("_err") == 403)
# 3b. 从"只能看"扩权为"可管理 material"
urow = next(u for u in call("GET", "/api/admin/users", token=AT) if u["username"] == UNAME)
upd2 = call("PUT", f"/api/admin/users/{urow['id']}",
            {"view_pages": ["dashboard", "material", "qcstandard"],
             "manage_modules": ["material"]}, token=AT)
check("动态扩权成功", upd2.get("ok") is True)
lg2 = call("POST", "/api/auth/login", {"username": UNAME, "password": "123456"})
m2 = next(m for m in lg2["menus"] if m["key"] == "material")
check("扩权后 material 可管理", m2["manage"] is True)
ok_write = call("POST", "/api/material", {"code": f"RX2-{TS}", "name": "扩权后写入", "material_type": "原料",
                                          "spec": "", "unit": "t"}, token=lg2.get("token", ""))
check("扩权后写物料成功", ok_write.get("ok") is True, str(ok_write.get("_detail", "")))
# 清理测试物料
if ok_write.get("ok"):
    call("DELETE", f"/api/material/{ok_write['id']}", token=AT)
# 3c. 显式传 null → 回角色默认（"套用角色默认"=前端传 null）
urow = next(u for u in call("GET", "/api/admin/users", token=AT) if u["username"] == UNAME)
back = call("PUT", f"/api/admin/users/{urow['id']}",
            {"view_pages": None, "manage_modules": None}, token=AT)
urow2 = next(u for u in call("GET", "/api/admin/users", token=AT) if u["username"] == UNAME)
check("传null→view_pages=null(回角色默认)", urow2["view_pages"] is None and urow2["manage_modules"] is None,
      f"view={urow2['view_pages']} manage={urow2['manage_modules']}")
lg3 = call("POST", "/api/auth/login", {"username": UNAME, "password": "123456"})
check("登录回到角色默认10页", keys(lg3) == sorted(
    ["dashboard", "screen", "prodlot", "qcstandard", "incoming", "ncr", "trace", "report", "material", "equipment"]), str(keys(lg3)))
# 3d. 显式空数组 = 一个页面都不给看
urow = next(u for u in call("GET", "/api/admin/users", token=AT) if u["username"] == UNAME)
empty = call("PUT", f"/api/admin/users/{urow['id']}", {"view_pages": [], "manage_modules": []}, token=AT)
urow3 = next(u for u in call("GET", "/api/admin/users", token=AT) if u["username"] == UNAME)
check("显式 [] 存为自定义空(非null)", urow3["view_pages"] == [], str(urow3["view_pages"]))
lg4 = call("POST", "/api/auth/login", {"username": UNAME, "password": "123456"})
check("严格空：登录无菜单(仅占位)", lg4.get("menus") == [], str(keys(lg4)))
# 清理
call("PUT", f"/api/admin/users/{urow['id']}", {"enabled": False}, token=AT)

print("══ 4. 停用/启用/删账号兼容 ══")
dis = call("PUT", f"/api/admin/users/{nuid}", {"enabled": False}, token=AT)
check("停用自定义权限账号", dis.get("ok") is True)
lgd = call("POST", "/api/auth/login", {"username": UNAME, "password": "123456"})
check("停用后登录 403", lgd.get("_err") == 403)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 多工序+动态权限验收全部通过")
