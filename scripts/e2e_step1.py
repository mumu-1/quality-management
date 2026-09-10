# -*- coding: utf-8 -*-
"""第1步 e2e 验收：登录权限 / 基础资料 CRUD / Excel 导入 / 账号停用拦截
运行: python scripts/e2e_step1.py
"""
import csv, io, json, os, sys, time, urllib.request, urllib.parse

TS = str(int(time.time()) % 100000)   # 每次运行唯一，保证脚本可重复运行
UCODE = 'RAW-' + TS
UNAME = 'testuser' + TS
BASE = "http://localhost:8000"


def call(method, path, body=None, token="", raw=False):
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("token", token)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if raw:
            return e.code
        return {"_err": e.code, "_detail": e.read().decode()[:200]}


PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


# ═══ 1. 登录与角色菜单 ═══
print("══ 1. 登录与角色权限 ══")
admin = call("POST", "/api/auth/login", {"username": "admin", "password": "123456"})
check("admin 登录成功", "token" in admin)
AT = admin.get("token", "")

qm = call("POST", "/api/auth/login", {"username": "qm", "password": "123456"})
qc = call("POST", "/api/auth/login", {"username": "qc", "password": "123456"})
buyer = call("POST", "/api/auth/login", {"username": "buyer", "password": "123456"})
store = call("POST", "/api/auth/login", {"username": "store", "password": "123456"})
bad = call("POST", "/api/auth/login", {"username": "admin", "password": "wrong"})
check("错误密码被拒", bad.get("_err") == 401)

def keys(d):
    return sorted([m["key"] for m in d.get("menus", [])])

check("admin 可见全部22页", keys(admin) == sorted(
    ["dashboard", "screen", "board", "prodlot", "qcstandard", "incoming", "ncr", "trace", "report",
     "complaint", "material", "supplier", "customer", "workshop", "station", "team", "equipment",
     "department", "position", "user", "duty", "audit"]), str(keys(admin)))
check("qc(检验员) 可见11页且无账号管理", keys(qc) == sorted(
    ["dashboard", "screen", "prodlot", "qcstandard", "incoming", "ncr", "trace", "report",
     "complaint", "material", "equipment"]), str(keys(qc)))
check("store(仓储) 无供应商管理", "supplier" not in keys(store), str(keys(store)))
check("buyer(采购) 可管理 supplier", any(m["key"] == "supplier" and m["manage"] for m in buyer.get("menus", [])))
check("qm(质量经理) 可管理 material", any(m["key"] == "material" and m["manage"] for m in qm.get("menus", [])))
check("qc(检验员) 无任何管理权", not any(m["manage"] for m in qc.get("menus", [])))

# 无 token 访问被拒
noauth = call("GET", "/api/material")
check("无 token 访问被拒 401", noauth.get("_err") == 401)
# 越权：qc 尝试写物料
qc_t = qc.get("token", "")
over = call("POST", "/api/material", {"code": "RAW-099", "name": "越权测试"}, token=qc_t)
check("qc 写物料被拒 403（越权拦截）", over.get("_err") == 403, str(over))

# ═══ 2. 基础资料 CRUD ═══
print("══ 2. 基础资料 CRUD ══")
mats = call("GET", "/api/material", token=AT)
check("物料列表含演示数据(≥9条，可重复运行)", len(mats) >= 9, f"实际 {len(mats)}")
names = [m["name"] for m in mats]
for expect in ["七水硫酸亚铁", "磷酸一铵", "85%磷酸", "双氧水", "木质纤维素", "硅藻土", "硫酸"]:
    check(f"物料含 {expect}", expect in names)

ws = call("GET", "/api/workshop", token=AT)
st = call("GET", "/api/station", token=AT)
eq = call("GET", "/api/equipment", token=AT)
check("车间5个 工序10个 设备13个", len(ws) == 5 and len(st) == 10 and len(eq) == 13,
      f"车间{len(ws)} 工序{len(st)} 设备{len(eq)}")
check("工序带车间名(JOIN 正常)", st and st[0].get("workshop_name"))
check("设备带工序名+车间名(JOIN 正常)", eq and eq[0].get("station_name") and eq[0].get("workshop_name"))

# 新增-编辑-停用（物料）
add = call("POST", "/api/material", {"code": UCODE, "name": "测试原料", "material_type": "原料",
                                     "spec": "测试规格", "unit": "t"}, token=AT)
check("新增物料成功", add.get("ok") is True, str(add))
dup = call("POST", "/api/material", {"code": "RAW-001", "name": "重复编码"}, token=AT)
check("重复编码被拒 400", dup.get("_err") == 400)
mid = add.get("id")
upd = call("PUT", f"/api/material/{mid}", {"code": UCODE, "name": "测试原料改名"}, token=AT)
check("编辑物料成功", upd.get("ok") is True)
del_ = call("DELETE", f"/api/material/{mid}", token=AT)
check("停用物料成功(软删)", del_.get("ok") is True)
mats2 = call("GET", "/api/material", token=AT)
check("停用后列表不含该物料", all(m["code"] != UCODE for m in mats2))

# 关键字搜索
sr = call("GET", "/api/material?" + urllib.parse.urlencode({"keyword": "磷酸"}), token=AT)
check("关键字搜索'磷酸'命中4条演示数据(含磷酸铁成品)", len(sr) == 4, f"{len(sr)} 条")

# ═══ 3. Excel 导入（CSV 模拟，30 行） ═══
print("══ 3. Excel/CSV 批量导入 ══")
buf = io.StringIO()
w = csv.writer(buf)
w.writerow(["编码", "名称", "类型(原料/辅料/中间品/成品)", "规格", "单位", "备注"])
for i in range(1, 31):
    w.writerow([f"IMP{TS}-{i:03d}", f"批量原料{i}", "原料", "含量≥98%", "t", "导入测试"])
# 留一个错误行：缺编码
w.writerow(["", "缺编码原料", "原料", "", "t", ""])
csv_text = buf.getvalue().encode("utf-8-sig")

boundary = "----qmsboundary"
body = b""
body += f"--{boundary}\r\n".encode()
body += b'Content-Disposition: form-data; name="file"; filename="mats.csv"\r\n'
body += b"Content-Type: text/csv\r\n\r\n"
body += csv_text + b"\r\n"
body += f"--{boundary}--\r\n".encode()
req = urllib.request.Request(BASE + "/api/import/material", data=body, method="POST")
req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
req.add_header("token", AT)
with urllib.request.urlopen(req) as r:
    imp = json.loads(r.read().decode())
check("导入30行成功+1行缺编码报错", imp.get("inserted") == 30 and imp.get("error_count") == 1,
      json.dumps(imp, ensure_ascii=False)[:150])
mats3 = call("GET", "/api/material", token=AT)
check("导入后物料数=基准+30", len(mats3) == len(mats2) + 30, f"{len(mats3)} vs 基准{len(mats2)}+30")

# 模板下载
req = urllib.request.Request(BASE + "/api/templates/material.csv")
req.add_header("token", AT)
with urllib.request.urlopen(req) as r:
    tpl = r.read().decode("utf-8-sig")
check("模板下载含表头", "编码" in tpl and "名称" in tpl)

# ═══ 4. 账号管理 + 停用拦截 ═══
print("══ 4. 账号管理 ══")
newu = call("POST", "/api/admin/users", {"username": UNAME, "real_name": "测试员",
                                         "department": "质量部", "role_key": "qc",
                                         "station_id": st[0]["id"], "password": "123456"}, token=AT)
check("新建账号成功", newu.get("ok") is True)
login_new = call("POST", "/api/auth/login", {"username": UNAME, "password": "123456"})
check("新账号可登录", "token" in login_new)
dis = call("PUT", "/api/admin/users/" + str(login_new["user"]["id"]), {"enabled": False}, token=AT)
check("停用账号成功", dis.get("ok") is True)
login_dis = call("POST", "/api/auth/login", {"username": UNAME, "password": "123456"})
check("停用后登录被拒 403", login_dis.get("_err") == 403, str(login_dis))
pwd = call("PUT", f"/api/admin/users/{login_new['user']['id']}/password", {"password": "abc12345"}, token=AT)
check("重置密码成功", pwd.get("ok") is True)
login_pwd = call("POST", "/api/auth/login", {"username": UNAME, "password": "abc12345"})
check("新密码可登录(但已停用→403, 说明停用优先)", login_pwd.get("_err") == 403)

# 总览
ov = call("GET", "/api/overview", token=AT)
check("总览计数正常", ov.get("material") == len(mats3) and ov.get("station") == 10)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第1步验收全部通过")
