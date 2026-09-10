# -*- coding: utf-8 -*-
"""第7步 e2e 验收：分权责管理（部门 + 职务 → 模块权限）
覆盖：
 1) 部门/职务 基础资料 CRUD（重名拒绝、被引用禁止删除）
 2) 岗位职责模板：保存/覆盖/越界校验(可管必须可见)/停用
 3) 账号挂岗位 → 权限来源=岗位职责；菜单与模板一致
 4) 三层优先级：账号微调 > 岗位职责 > 角色默认（含显式 null 回落岗位职责）
 5) 改职责模板 → 相关账号权限即时变化（分权责的核心价值）
 6) 权限拦截：非管理员看不到岗位职责/账号管理
 7) 通用职务兜底（部门未单独配置时生效）
运行: python scripts/e2e_step7.py （先启动服务）
注意：会写测试数据（部门/职务/职责/账号），跑完请重置演示库。
"""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"
TS = str(int(time.time()) % 100000)
PASS, FAIL = [], []


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


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


def login(u, p="123456"):
    return call("POST", "/api/auth/login", {"username": u, "password": p})


AT = login("admin").get("token", "")
check("管理员登录", bool(AT))

print("══ 1. 部门 / 职务 基础资料 ══")
deps = call("GET", "/api/department", token=AT)
poss = call("GET", "/api/position", token=AT)
check("演示部门已就位（≥8 个）", len(deps) >= 8, f"{len(deps)} 个")
check("演示职务已就位（≥15 个）", len(poss) >= 15, f"{len(poss)} 个")
check("部门含质量部/生产部", {"质量部", "生产部"} <= {d["name"] for d in deps})
check("职务含检验员/班组长/操作工", {"检验员", "班组长", "操作工"} <= {p["name"] for p in poss})
# 新增（唯一编码）
nd = call("POST", "/api/department", {"code": f"DEP-{TS}", "name": f"测试事业部{TS}"}, token=AT)
check("新增部门成功", nd.get("ok") is True, str(nd))
np_ = call("POST", "/api/position", {"code": f"POS-{TS}", "name": f"测试岗{TS}"}, token=AT)
check("新增职务成功", np_.get("ok") is True, str(np_))
dup = call("POST", "/api/department", {"code": f"DEP-{TS}", "name": "重复编码"}, token=AT)
check("部门重复编码被拒 400", dup.get("_err") == 400)
# 编辑（部分更新不清空其它字段）
ed = call("PUT", f"/api/department/{nd['id']}", {"name": f"测试事业部{TS}改名"}, token=AT)
after = [d for d in call("GET", "/api/department", token=AT) if d["id"] == nd["id"]]
check("部门可编辑（改名后编码保留）", ed.get("ok") and after and after[0]["name"].endswith("改名")
      and after[0]["code"] == f"DEP-{TS}", str(after[0]) if after else "")

print("══ 2. 岗位职责模板 ══")
dm = call("GET", "/api/duty-matrix", token=AT)
check("职责矩阵返回部门/职务/模板/模块", all(k in dm for k in
      ("departments", "positions", "templates", "modules", "roles")))
check("内置起步模板已种入（≥15 条）", len(dm["templates"]) >= 15, f"{len(dm['templates'])} 条")
check("模块清单含新增页面", {"department", "position", "duty"} <= {m["key"] for m in dm["modules"]})
op = next(p for p in poss if p["name"] == "操作工")
qr = next(d for d in deps if d["name"] == "质量部")
# 保存：质量部·操作工 = 总览+生产+大屏
sv = call("POST", "/api/duty-templates",
          {"dept_id": qr["id"], "position_id": op["id"],
           "view_pages": ["dashboard", "prodlot", "screen"], "manage_modules": []}, token=AT)
check("保存岗位职责成功", sv.get("ok") is True, str(sv))
dm2 = call("GET", "/api/duty-matrix", token=AT)
t = next((x for x in dm2["templates"] if x["dept_id"] == qr["id"] and x["position_id"] == op["id"]), None)
check("模板可读回且内容一致", t and sorted(t["view_pages"]) == ["dashboard", "prodlot", "screen"])
# 覆盖保存（同组合再存 = 更新而非新增）
sv2 = call("POST", "/api/duty-templates",
           {"dept_id": qr["id"], "position_id": op["id"],
            "view_pages": ["dashboard", "prodlot", "screen"], "manage_modules": ["prodlot"]}, token=AT)
dm3 = call("GET", "/api/duty-matrix", token=AT)
cnt = len([x for x in dm3["templates"] if x["dept_id"] == qr["id"] and x["position_id"] == op["id"]])
t2 = next(x for x in dm3["templates"] if x["dept_id"] == qr["id"] and x["position_id"] == op["id"])
check("同组合覆盖不重复新增", cnt == 1 and sv2.get("id") == sv.get("id"))
check("覆盖后内容已更新", sorted(t2["view_pages"]) == ["dashboard", "prodlot", "screen"]
      and t2["manage_modules"] == ["prodlot"])
check("部门专属模板已区别于通用兜底(2个模块)", len(t2["view_pages"]) == 3)
# 校验
bad1 = call("POST", "/api/duty-templates",
            {"dept_id": qr["id"], "position_id": op["id"], "view_pages": [], "manage_modules": []}, token=AT)
check("空可见模块被拒 400", bad1.get("_err") == 400, bad1.get("_detail", "")[:60])
bad2 = call("POST", "/api/duty-templates",
            {"dept_id": qr["id"], "position_id": op["id"],
             "view_pages": ["dashboard"], "manage_modules": ["ncr"]}, token=AT)
check("可管模块必须可见（越界被拒 400）", bad2.get("_err") == 400, bad2.get("_detail", "")[:60])
bad3 = call("POST", "/api/duty-templates",
            {"dept_id": 999999, "position_id": op["id"], "view_pages": ["dashboard"],
             "manage_modules": []}, token=AT)
check("部门不存在被拒 400", bad3.get("_err") == 400)

print("══ 3. 账号挂岗位 → 权限来自岗位职责 ══")
uname = f"opuser{TS}"
nu = call("POST", "/api/admin/users",
          {"username": uname, "real_name": "测试操作工", "password": "123456",
           "dept_id": qr["id"], "position_id": op["id"], "role_key": "worker"}, token=AT)
check("新建账号（带部门+职务）成功", nu.get("ok") is True, str(nu))
lg = login(uname)
check("新账号可登录", bool(lg.get("token")))
check("★ 权限来源 = 岗位职责", lg["user"]["perm_source"] == "duty", lg["user"]["perm_source"])
check("部门/职务回显正确", lg["user"]["dept_name"] == "质量部" and lg["user"]["position_name"] == "操作工",
      f"{lg['user']['dept_name']}/{lg['user']['position_name']}")
menus = sorted(m["key"] for m in lg["menus"])
check("菜单=岗位职责模板（总览/生产/大屏）", menus == ["dashboard", "prodlot", "screen"], str(menus))
check("部门名已冗余同步到 department 字段", lg["user"]["department"] == "质量部")
urow = next((x for x in call("GET", "/api/admin/users", token=AT) if x["username"] == uname), None)
check("账号列表显示部门/职务/来源", urow and urow["dept_name"] == "质量部"
      and urow["position_name"] == "操作工" and urow["perm_source"] == "duty")

print("══ 4. 三层优先级：账号微调 > 岗位职责 > 角色默认 ══")
uid = nu["id"]
call("PUT", f"/api/admin/users/{uid}", {"view_pages": ["dashboard"], "manage_modules": []}, token=AT)
lg2 = login(uname)
check("账号微调覆盖岗位职责", sorted(m["key"] for m in lg2["menus"]) == ["dashboard"]
      and lg2["user"]["perm_source"] == "account")
call("PUT", f"/api/admin/users/{uid}", {"view_pages": None, "manage_modules": None}, token=AT)
lg3 = login(uname)
check("★ 显式清空后回落到岗位职责", lg3["user"]["perm_source"] == "duty"
      and sorted(m["key"] for m in lg3["menus"]) == ["dashboard", "prodlot", "screen"],
      str(sorted(m["key"] for m in lg3["menus"])))
check("★ 部门专属优先于通用兜底（同职务不同部门结果不同）",
      sorted(m["key"] for m in lg3["menus"]) != ["dashboard", "prodlot"])

print("══ 5. 改职责模板 → 相关账号即时跟随（分权责核心）══")
call("POST", "/api/duty-templates",
     {"dept_id": qr["id"], "position_id": op["id"],
      "view_pages": ["dashboard", "prodlot", "screen", "trace"], "manage_modules": []}, token=AT)
lg4 = login(uname)
check("★ 模板加模块后账号菜单同步变化",
      sorted(m["key"] for m in lg4["menus"]) == ["dashboard", "prodlot", "screen", "trace"]
      and lg4["user"]["perm_source"] == "duty", str(sorted(m["key"] for m in lg4["menus"])))
check("新增的追溯页可访问", not call("GET", "/api/trace?lot_no=X", token=lg4["token"]).get("_err") == 403)

print("══ 6. 通用职务兜底 ══")
# 造一个新部门 + 已有"操作工"通用模板（种子已种 None/POS-OP）
ndep = call("POST", "/api/department", {"code": f"DEP2-{TS}", "name": f"兜底测试部{TS}"}, token=AT)
u2 = f"opuser2{TS}"
call("POST", "/api/admin/users",
     {"username": u2, "real_name": "兜底测试", "password": "123456",
      "dept_id": ndep["id"], "position_id": op["id"], "role_key": "worker"}, token=AT)
lg5 = login(u2)
check("★ 部门未配职责时用通用职务兜底", lg5["user"]["perm_source"] == "duty"
      and sorted(m["key"] for m in lg5["menus"]) == ["dashboard", "prodlot"],
      str(sorted(m["key"] for m in lg5["menus"])))
check("兜底≠上面新配的（说明走的是通用模板）",
      sorted(m["key"] for m in lg5["menus"]) != ["dashboard", "prodlot", "screen"])

print("══ 7. 权限拦截 + 引用保护 ══")
st = login("store").get("token", "")
check("库管无岗位职责页权限 403", call("GET", "/api/duty-matrix", token=st).get("_err") == 403)
check("库管无账号管理权限 403", call("GET", "/api/admin/users", token=st).get("_err") == 403)
check("库管不能存职责模板 403",
      call("POST", "/api/duty-templates",
           {"position_id": op["id"], "view_pages": ["dashboard"], "manage_modules": []},
           token=st).get("_err") == 403)
check("库管不能新增部门 403",
      call("POST", "/api/department", {"code": "X", "name": "X"}, token=st).get("_err") == 403)
qq = login("qc").get("token", "")
check("检验员无岗位职责页权限 403", call("GET", "/api/duty-matrix", token=qq).get("_err") == 403)
# 被引用不能删
dd = call("DELETE", f"/api/department/{qr['id']}", token=AT)
check("有人用的部门不能删除 400", dd.get("_err") == 400, dd.get("_detail", "")[:60])
dp = call("DELETE", f"/api/position/{op['id']}", token=AT)
check("配了职责的职务不能删除 400", dp.get("_err") == 400, dp.get("_detail", "")[:60])
# 未被引用的可删（软删）——用新建的干净部门（上面那个里已经建了账号）
ndep2 = call("POST", "/api/department", {"code": f"DEP3-{TS}", "name": f"可删测试部{TS}"}, token=AT)
ddq = call("DELETE", f"/api/department/{ndep2['id']}", token=AT)
check("无引用的部门可停用", ddq.get("ok") is True, str(ddq))
gone = [d for d in call("GET", "/api/department", token=AT) if d["id"] == ndep2["id"]]
check("停用后不再出现在列表", len(gone) == 0)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第7步（分权责管理）验收全部通过")
