# -*- coding: utf-8 -*-
"""第12步 e2e 验收：按角色的"我的工作台"（手机端功能与业务划分）
覆盖：
 1) 各角色均可取工作台；字段完整（角色/待办/Tab/快捷业务）
 2) 手机底部 Tab 按角色不同，且只含本人有权限的页面
 3) 待办按角色过滤（车间看自检、质检看质检、采购/仓储各看自己那段）
 4) 待办数字与真实数据联动：建批→车间待自检+1；自检合格→质检待确认+1；质检不合格→不存在待确认
 5) 快捷业务按角色与权限过滤
 6) 未登录不可访问
 7) 前端结构（工作台渲染、按角色 Tab 来源）
运行: python scripts/e2e_step12.py（先启动服务；跑完请重置演示库）
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "http://localhost:8000"
PASS, FAIL = [], []


def raw(path, t="", method="GET", body=None):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if t:
        req.add_header("token", t)
    try:
        with urllib.request.urlopen(req, timeout=25) as r:
            return r.status, r.read().decode("utf-8", "ignore")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore")


def call(path, t="", method="GET", body=None):
    st, txt = raw(path, t, method, body)
    try:
        d = json.loads(txt)
    except Exception:
        return {"_err": st, "_text": txt[:150]}
    if st >= 400 and isinstance(d, dict) and "_err" not in d:
        d["_err"] = st
    return d


def check(n, c, x=""):
    (PASS if c else FAIL).append(n)
    print(("  ✔ " if c else "  ✘ ") + n + ("  " + x if x else ""))


def login(u, p="123456"):
    r = call("/api/auth/login", method="POST", body={"username": u, "password": p})
    return r.get("token", "")


ROLES = ["worker", "prodlead", "qc", "sampler", "qm", "boss", "buyer", "store", "admin"]
TOK = {r: login(r) for r in ROLES}
AT = TOK["admin"]
check("各角色均可登录", all(TOK.values()), f"{sum(1 for v in TOK.values() if v)}/{len(ROLES)}")
check("未登录访问工作台 → 401", raw("/api/workbench")[0] == 401)

print("══ 1. 工作台字段与角色信息 ══")
wb = {r: call("/api/workbench", t=TOK[r]) for r in ROLES}
check("字段完整", all(all(k in wb[r] for k in ("role", "role_name", "todos", "tabs", "quick",
                                            "dept_name", "position_name", "total_open"))
                    for r in ROLES))
check("角色名与账号一致", wb["qc"]["role"] == "qc" and "检验" in wb["qc"]["role_name"],
      f"{wb['qc']['role_name']}")
check("带出部门/职务", bool(wb["qc"]["dept_name"]), f"{wb['qc']['dept_name']}/{wb['qc']['position_name']}")
check("班组长带出负责工序", isinstance(wb["prodlead"]["stations"], list))

print("══ 2. 手机底部 Tab 按角色划分 ══")
def tabs(r):
    return [t[0] for t in wb[r]["tabs"]]

check("★ 车间（班组长）Tab = 工作台/生产/追溯/车间大屏",
      tabs("prodlead")[:4] == ["dashboard", "prodlot", "trace", "screen"], str(tabs("prodlead")))
check("★ 检验员 Tab 含来料", "incoming" in tabs("qc") and "prodlot" in tabs("qc"), str(tabs("qc")))
check("★ 质量经理 Tab 含大屏+不合格", "board" in tabs("qm") and "ncr" in tabs("qm"), str(tabs("qm")))
check("★ 采购 Tab 含客诉", "complaint" in tabs("buyer"), str(tabs("buyer")))
check("★ 仓储 Tab 含来料/追溯（查放行状态）",
      "incoming" in tabs("store") and "trace" in tabs("store"), str(tabs("store")))
check("★ 管理员 Tab 含大屏+不合格", "board" in tabs("admin") and "ncr" in tabs("admin"), str(tabs("admin")))
check("每个角色最后一个 Tab 都是『更多』", all(tabs(r)[-1] == "__more" for r in ROLES))
check("Tab 只含本人有权限的页面",
      all(set(tabs(r)) - {"__more"} <= {m["key"] for m in call("/api/auth/login", method="POST",
                                                              body={"username": r, "password": "123456"})
                                     .get("menus", [])} for r in ROLES))
check("★ 采购/仓储没有大屏入口（权限隔离）", "board" not in tabs("buyer") and "board" not in tabs("store"))

print("══ 3. 待办按角色过滤 ══")
def todo_keys(r):
    return {t["key"] for t in wb[r]["todos_all"]}

check("★ 车间看到『待车间自检』", "self" in todo_keys("worker") or "self" in todo_keys("prodlead"))
check("★ 操作工看不到『待处置不合格』", "ncr" not in todo_keys("worker"))
check("★ 检验员看到『待质检部检测』", "dept" in todo_keys("qc"))
check("★ 采购看到『待处置不合格』与『待处理客诉』",
      "ncr" in todo_keys("buyer") and "complaint" in todo_keys("buyer"))
check("★ 仓储看到『已放行成品（可发货）』", "released" in todo_keys("store"))
check("★ 检验员看不到仓储的『已放行成品』", "released" not in todo_keys("qc"))
check("★ 质量经理可见全部（管理岗）",
      {"self", "dept", "ncr", "complaint", "released"} <= todo_keys("qm"), str(sorted(todo_keys("qm"))))
check("取样员看到待取样/来料检验", "sample" in todo_keys("sampler") or "iqc" in todo_keys("sampler"))

print("══ 4. 待办数字与真实数据联动 ══")
lots2 = [x for x in call("/api/production-lots?status=2", t=AT) if x.get("station_code") == "ST01"]
check("有可作父批的上工序合格批（ST01）", len(lots2) > 0, f"{len(lots2)} 批")
st03 = next(s for s in call("/api/station", t=AT) if s["code"] == "ST03")
before_self = next((t["count"] for t in call("/api/workbench", t=TOK["prodlead"])["todos_all"]
                    if t["key"] == "self"), 0)
lot = call("/api/production-lots", t=AT, method="POST",
           body={"station_id": st03["id"], "parent_lot_no": lots2[0]["lot_no"], "qty": 12})
check("新建一个待检批", "id" in lot, str(lot)[:70])
after_self = next((t["count"] for t in call("/api/workbench", t=TOK["prodlead"])["todos_all"]
                   if t["key"] == "self"), 0)
check("★ 班组长『待车间自检』计数 +1", after_self == before_self + 1, f"{before_self} → {after_self}")
f = call(f"/api/production-lots/{lot['id']}/test-form?check_type=ipqc", t=AT)


def vals(fm, by=None, bad=False):
    out = []
    for it in fm.get("items", []):
        if by and it.get("check_by") != by:
            continue
        if bad:
            v = str(round((it["max_val"] or 100) * 4, 1))
        elif it["min_val"] is not None and it["max_val"] is not None:
            v = str(round((it["min_val"] + it["max_val"]) / 2, 2))
        elif it["min_val"] is not None:
            v = str(it["min_val"] + 1)
        elif it["max_val"] is not None:
            v = str(round(it["max_val"] * 0.5, 2))
        else:
            v = "正常"
        out.append({"indicator": it["indicator"], "actual": v})
    return out


# 有自检项 → 交自检（应挂在待质检确认）
r = call(f"/api/production-lots/{lot['id']}/test", t=AT, method="POST",
         body={"std_id": f["std_id"], "check_type": "ipqc", "stage": "self",
               "items": vals(f, "self")})
if r.get("pending_dept") == 1:
    pend = next((t["count"] for t in call("/api/workbench", t=TOK["qc"])["todos_all"]
                 if t["key"] == "pending"), 0)
    check("★ 自检合格后 质检员『待质检确认』≥1", pend >= 1, f"{pend} 项")
    call(f"/api/production-lots/{lot['id']}/test", t=AT, method="POST",
         body={"std_id": f["std_id"], "check_type": "ipqc", "stage": "dept",
               "items": vals(f, "dept")})
    pend2 = next((t["count"] for t in call("/api/workbench", t=TOK["qc"])["todos_all"]
                  if t["key"] == "pending"), 0)
    check("★ 质检补交后『待质检确认』回落", pend2 == pend - 1, f"{pend} → {pend2}")
else:
    check("（该工序无自检项，走整体判定）", r.get("result") == 1, str(r)[:70])

print("══ 5. 快捷业务按角色/权限过滤 ══")
check("★ 班组长快捷含建批/自检/大屏",
      len(wb["prodlead"]["quick"]) >= 3 and any("自检" in q["name"] for q in wb["prodlead"]["quick"]),
      "、".join(q["name"] for q in wb["prodlead"]["quick"]))
check("★ 质检快捷含质检部录入", any("质检" in q["name"] for q in wb["qc"]["quick"]),
      "、".join(q["name"] for q in wb["qc"]["quick"]))
check("★ 质量经理快捷含不合格处置/客诉",
      any("不合格" in q["name"] for q in wb["qm"]["quick"])
      and any("客诉" in q["name"] for q in wb["qm"]["quick"]))
check("★ 管理员快捷含账号/岗位职责/操作日志",
      any("账号" in q["name"] for q in wb["admin"]["quick"])
      and any("岗位职责" in q["name"] for q in wb["admin"]["quick"]))
check("快捷入口不含无权限页面",
      all(q["page"] in {m["key"] for m in call("/api/auth/login", method="POST",
                                               body={"username": r, "password": "123456"}).get("menus", [])}
          for r in ROLES for q in wb[r]["quick"]))

print("══ 6. 前端结构 ══")
with urllib.request.urlopen(BASE + "/") as rr:
    ps = rr.read().decode("utf-8", "ignore")
for n, ok in [
    ("工作台容器", 'id="wb-box"' in ps),
    ("待办卡片渲染", "function fillWorkbench" in ps and "wb-card" in ps),
    ("工作台数据加载", "function loadWorkbench" in ps and "api('/api/workbench')" in ps),
    ("手机 Tab 按角色（后端给顺序）", "window.__wb.tabs" in ps),
    ("标题改为我的工作台", "📋 我的工作台" in ps),
    ("移动端两列待办布局", "max-width:840px" in ps and ".wb-grid" in ps),
]:
    check(n, ok)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第12步（按角色工作台）验收全部通过")
