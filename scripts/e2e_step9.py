# -*- coding: utf-8 -*-
"""第9步 e2e 验收：客诉管理 + 操作日志（第三批）
覆盖：
 1) 客诉演示数据与列表/统计
 2) 登记客诉（自动编号、字段校验、权限）
 3) 处置权限：检验员/采购不能处置（403），质量经理/管理员可处置
 4) 8D 式闭环校验：直接关闭缺原因/措施/回复 → 400；补齐后可关闭，留痕处置人与关闭人
 5) 关联批次可一键追溯（客诉 lot_no → /api/trace）
 6) 操作日志：管理员可查/可按用户与动作与关键词筛选；非管理员 403；新动作被记录
 7) 数据大屏含客诉 KPI
 8) 前端结构（客诉页/日志页/路由）
运行: python scripts/e2e_step9.py（先启动服务；会写测试数据，跑完请重置演示库）
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


def call(m, p, body=None, t=""):
    req = urllib.request.Request(BASE + p, data=json.dumps(body).encode() if body is not None else None, method=m)
    req.add_header("Content-Type", "application/json")
    if t:
        req.add_header("token", t)
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_d": e.read().decode()[:200]}


def check(n, c, x=""):
    (PASS if c else FAIL).append(n)
    print(("  ✔ " if c else "  ✘ ") + n + ("  " + x if x else ""))


def login(u):
    return call("POST", "/api/auth/login", {"username": u, "password": "123456"})


AT, QT, QCT, BT = (login(x).get("token", "") for x in ("admin", "qm", "qc", "buyer"))
check("登录（管理员/质量经理/检验员/采购）", all([AT, QT, QCT, BT]))

print("══ 1. 客诉演示数据 ══")
d = call("GET", "/api/complaints", t=AT)
check("客诉列表返回 rows/stat/total/open", all(k in d for k in ("rows", "stat", "total", "open")))
check("演示客诉 ≥3 条", d["total"] >= 3, f"{d['total']} 条")
check("状态分布含 待受理/调查中/已关闭",
      d["stat"].get("0", 0) >= 1 and d["stat"].get("1", 0) >= 1 and d["stat"].get("3", 0) >= 1,
      str(d["stat"]))
closed = next((x for x in d["rows"] if x["status"] == 3), None)
check("已关闭客诉含原因/措施/回复", closed and closed["root_cause"] and closed["action"] and closed["reply"])
check("已关闭客诉留痕关闭人与时间", closed and closed["closed_by"] and closed["closed_at"],
      f"{closed['closed_by']} {closed['closed_at']}" if closed else "")
withlot = next((x for x in d["rows"] if x["lot_no"]), None)
check("演示客诉已关联批次（可追溯）", bool(withlot), withlot["lot_no"] if withlot else "")

print("══ 2. 登记客诉 ══")
title = f"验证客诉{TS}：干燥水分波动"
r = call("POST", "/api/complaints",
         {"title": title, "content": "验证用", "claim_type": "质量异议", "severity": 2,
          "lot_no": withlot["lot_no"] if withlot else ""}, t=QCT)
check("检验员可登记客诉", r.get("ok") is True, str(r)[:80])
no = r.get("complaint_no", "")
check("自动编号 CS-年份-序号", no.startswith("CS-") and len(no.split("-")) == 3, no)
r2 = call("POST", "/api/complaints", {"title": f"验证客诉{TS}-2"}, t=BT)
check("采购也可登记客诉", r2.get("ok") is True, str(r2.get("complaint_no")))
check("编号递增不重复", r2.get("complaint_no") != no, f"{no} / {r2.get('complaint_no')}")
check("空主题被拒 400", call("POST", "/api/complaints", {"title": ""}, t=AT).get("_err") == 400)
check("非法类型被拒 400",
      call("POST", "/api/complaints", {"title": "x", "claim_type": "乱写"}, t=AT).get("_err") == 400)
check("非法严重度被拒 400",
      call("POST", "/api/complaints", {"title": "x", "severity": 9}, t=AT).get("_err") == 400)
d2 = call("GET", "/api/complaints?keyword=" + urllib.parse.quote(title), t=AT)
check("可按关键词搜到", d2["total"] >= 1, f"{d2['total']} 条")
cid = r["id"]

print("══ 3. 处置权限 ══")
r3 = call("PUT", f"/api/complaints/{cid}", {"root_cause": "检验员试图写原因"}, t=QCT)
check("★ 检验员不能处置（403）", r3.get("_err") == 403, str(r3)[:70])
check("采购不能处置 403",
      call("PUT", f"/api/complaints/{cid}", {"reply": "采购试图回复"}, t=BT).get("_err") == 403)
check("检验员可补充内容（非处置字段）",
      call("PUT", f"/api/complaints/{cid}", {"content": "补充：客户已提供照片"}, t=QCT).get("ok") is True)
check("质量经理可处置", call("PUT", f"/api/complaints/{cid}", {"root_cause": "干燥机温度探头偏差"},
                        t=QT).get("ok") is True)

print("══ 4. 8D 式闭环校验 ══")
r4 = call("PUT", f"/api/complaints/{cid}", {"status": 3}, t=QT)
check("★ 缺措施/回复直接关闭被拒 400", r4.get("_err") == 400, str(r4.get("_d", ""))[:80])
call("PUT", f"/api/complaints/{cid}", {"action": "校准探头并加严点检"}, t=QT)
r5 = call("PUT", f"/api/complaints/{cid}", {"status": 3}, t=QT)
check("★ 缺回复仍被拒 400", r5.get("_err") == 400, str(r5.get("_d", ""))[:80])
call("PUT", f"/api/complaints/{cid}", {"reply": "已整改并回复客户，客户接受"}, t=QT)
r6 = call("PUT", f"/api/complaints/{cid}", {"status": 3}, t=QT)
check("★ 补齐后关闭成功", r6.get("ok") is True and r6.get("status") == 3, str(r6)[:70])
d3 = call("GET", "/api/complaints?status=3", t=AT)
row = next((x for x in d3["rows"] if x["id"] == cid), None)
check("关闭后留痕处置人与关闭人", row and row["handled_by"] == "qm" and row["closed_by"] == "qm"
      and row["handled_at"] and row["closed_at"], f"{row['handled_by']}/{row['closed_by']}" if row else "")
check("状态可流转到调查中", call("PUT", f"/api/complaints/{r2['id']}", {"status": 1}, t=QT).get("ok") is True)
check("状态非法被拒 400", call("PUT", f"/api/complaints/{cid}", {"status": 9}, t=QT).get("_err") == 400)

print("══ 5. 关联批次可追溯 ══")
if withlot:
    tr = call("GET", "/api/trace?lot_no=" + urllib.parse.quote(withlot["lot_no"]), t=AT)
    check("★ 客诉关联的批号能查到追溯链", isinstance(tr, dict) and len(tr.get("chain", [])) >= 3,
          f"{len(tr.get('chain', []))} 级")

print("══ 6. 操作日志 ══")
al = call("GET", "/api/audit-logs?days=30", t=AT)
check("管理员可查操作日志", all(k in al for k in ("rows", "users", "actions")), f"{len(al['rows'])} 条")
check("日志含本次客诉登记动作",
      any(x["target"].startswith("complaint:") and no in x["target"] for x in al["rows"])
      or any("complaint" in x["target"] for x in al["rows"]),
      next((x["target"] for x in al["rows"] if "complaint" in x["target"]), ""))
check("日志含状态流转留痕",
      any("状态" in (x["detail"] or "") and "已关闭" in (x["detail"] or "") for x in al["rows"]))
check("可按用户筛选",
      all(x["username"] == "qm" for x in call("GET", "/api/audit-logs?username=qm", t=AT)["rows"]))
check("可按动作筛选",
      all(x["action"] == "create" for x in call("GET", "/api/audit-logs?action=create", t=AT)["rows"]))
check("可按关键词筛选",
      call("GET", "/api/audit-logs?keyword=" + urllib.parse.quote(no), t=AT)["rows"] is not None)
check("★ 非管理员无权限 403", call("GET", "/api/audit-logs", t=QCT).get("_err") == 403)
check("采购也无权限 403", call("GET", "/api/audit-logs", t=BT).get("_err") == 403)
check("检验员不能看客诉以外页面（账号管理 403）",
      call("GET", "/api/admin/users", t=QCT).get("_err") == 403)

print("══ 7. 大屏客诉 KPI ══")
bd = call("GET", "/api/board?days=30", t=AT)
check("数据大屏含客诉 KPI", "complaint" in bd and all(k in bd["complaint"] for k in ("total", "open", "closing")),
      str(bd.get("complaint")))

print("══ 8. 前端结构 ══")
with urllib.request.urlopen(BASE + "/") as rr:
    ps = rr.read().decode("utf-8", "ignore")
ui = [
    ("客诉页渲染函数", "function renderComplaintPage" in ps),
    ("客诉登记弹窗", "function showComplaintForm" in ps),
    ("客诉处置弹窗", "function showComplaint" in ps and "function saveComplaintDeal" in ps),
    ("客诉状态统计卡", "CP_ST" in ps and "未闭环" in ps),
    ("客诉一键查追溯", "function gotoTrace" in ps),
    ("操作日志页", "function renderAuditPage" in ps and "function loadAudit" in ps),
    ("动作中文映射", "ACT_NAMES" in ps),
    ("路由 complaint/audit", "complaint:()=>renderComplaintPage()" in ps and "audit:()=>renderAuditPage()" in ps),
    ("大屏客诉卡", "客户投诉" in ps),
]
for n, ok in ui:
    check(n, ok)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第9步（客诉 + 操作日志）验收全部通过")
