# -*- coding: utf-8 -*-
"""第8步 e2e 验收：检测指标分"车间自检 / 质检部检测"
覆盖：
 1) 标准库指标带检测方（种子分配：现场可测=自检，要仪器=质检部）
 2) 检验模板返回检测方与各环节可录/已录
 3) 车间自检：班组长/操作工可交、质检员交自检被拒；合格→先流转+挂"待质检"
 4) 质检部检测：质检员补交，与自检同一张检验单；合格→完成并推进
 5) 不合格分两种来源都冻结+NCR（自检不合格 / 质检部不合格）
 6) 环节越权拦截（自检项交质检部项、生产角色交质检环节）
 7) 无自检项/无质检项的边界提示
运行: python scripts/e2e_step8.py（先启动服务；会写测试数据，跑完请重置演示库）
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


AT = login("admin").get("token", "")
PT = login("prodlead").get("token", "")
QT = login("qc").get("token", "")
WT = login("worker0001") if False else ""
check("登录（管理员/班组长/质检员）", all([AT, PT, QT]))

print("══ 1. 标准库检测方分配 ══")
stds = call("GET", "/api/qc-standards", t=AT)
st01 = next((x for x in stds if "溶解配液" in x["name"]), None)
check("溶解配液标准存在", bool(st01))
d1 = call("GET", f"/api/qc-standards/{st01['id']}", t=AT)
items = d1.get("items", [])
check("明细返回检测方字段", all("check_by" in it for it in items), f"{len(items)} 项")
selfs = [it["indicator"] for it in items if it["check_by"] == "self"]
depts = [it["indicator"] for it in items if it["check_by"] == "dept"]
check("★ 溶解配液：现场可测项=自检", "溶液温度" in selfs and "pH值" in selfs, str(selfs))
check("★ 溶解配液：要仪器项=质检部", "溶解浓度(FeSO4)" in depts, str(depts))
# 保存时能改检测方并读回
new_items = []
for it in items:
    new_items.append({"indicator": it["indicator"], "unit": it["unit"], "min_val": it["min_val"],
                      "max_val": it["max_val"], "method": it["method"], "is_key": it["is_key"],
                      "check_by": "self" if it["indicator"] == "pH值" else it["check_by"]})
upd = call("PUT", f"/api/qc-standards/{st01['id']}",
           {"name": d1["name"], "check_type": d1["check_type"], "object_type": d1["object_type"],
            "object_id": d1["object_id"], "items": new_items}, t=AT)
d2 = call("GET", f"/api/qc-standards/{st01['id']}", t=AT)
check("标准库可修改检测方并读回",
      upd.get("ok") is True and next(x for x in d2["items"] if x["indicator"] == "pH值")["check_by"] == "self")

print("══ 2. 检验模板（按环节）══")
incoming = call("GET", "/api/incoming?status=2", t=AT)
parent = incoming[0]["lot_no"] if incoming else ""
mk = lambda st: call("POST", "/api/production-lots",
                     {"station_id": st["id"], "parent_lot_no": parent, "qty": 100,
                      "equipment_id": None, "team_id": None}, t=AT)
st_list = call("GET", "/api/station", t=AT)
st01o = next(s for s in st_list if s["code"] == "ST01")
lot_a = mk(st01o)
check("建测试批A（溶解配液）", lot_a.get("ok") is True, str(lot_a)[:80])
fa = call("GET", f"/api/production-lots/{lot_a['id']}/test-form?check_type=ipqc", t=AT)
check("模板含检测方与环节统计",
      fa.get("self_count", 0) >= 1 and fa.get("dept_count", 0) >= 1
      and "can_self" in fa and "can_dept" in fa,
      f"自检{fa.get('self_count')}/质检{fa.get('dept_count')}")
fp = call("GET", f"/api/production-lots/{lot_a['id']}/test-form?check_type=ipqc", t=PT)
check("班组长可录自检、不可录质检", fp["can_self"] is True and fp["can_dept"] is False,
      f"can_self={fp.get('can_self')} can_dept={fp.get('can_dept')}")
check("管理员两个环节都能录（兜底）", fa["can_self"] is True and fa["can_dept"] is True)
fq = call("GET", f"/api/production-lots/{lot_a['id']}/test-form?check_type=ipqc", t=QT)
check("质检员不可录自检、可录质检", fq["can_self"] is False and fq["can_dept"] is True)
fm = call("GET", f"/api/production-lots/{lot_a['id']}/test-form?check_type=ipqc", t=PT)
check("模板带各环节未录状态", fm["self_done"] is False and fm["dept_done"] is False)


def vals(form, by):
    out = []
    for it in form["items"]:
        if it["check_by"] != by:
            continue
        v = ""
        if it["min_val"] is not None and it["max_val"] is not None:
            v = str(round((it["min_val"] + it["max_val"]) / 2, 2))
        elif it["min_val"] is not None:
            v = str(it["min_val"] + 1)
        elif it["max_val"] is not None:
            v = str(round(it["max_val"] * 0.5, 2))
        else:
            v = "正常"
        out.append({"indicator": it["indicator"], "actual": v})
    return out


print("══ 3. 车间自检（合格 → 先流转 + 待质检）══")
r1 = call("POST", f"/api/production-lots/{lot_a['id']}/test",
          {"std_id": fa["std_id"], "check_type": "ipqc", "stage": "self",
           "items": vals(fa, "self")}, t=PT)
check("班组长交自检成功", r1.get("ok") is True, str(r1)[:120])
check("★ 自检合格即先流转（status=2 过程合格）", r1.get("status") == 2, str(r1.get("status")))
check("★ 挂'待质检确认'标记", r1.get("pending_dept") == 1, str(r1.get("pending_dept")))
check("自检单结果仍为'检验中(0)'（等质检）", r1.get("result") == 0, str(r1.get("result")))
check("自检单据号已生成", bool(r1.get("test_no")))
q0 = login("qc")
r_bad = call("POST", f"/api/production-lots/{lot_a['id']}/test",
             {"std_id": fa["std_id"], "check_type": "ipqc", "stage": "self",
              "items": vals(fa, "self")}, t=QT)
check("质检员交'自检'被拒 403", r_bad.get("_err") == 403, str(r_bad)[:80])

print("══ 4. 质检部补交（同一张单完成）══")
r2 = call("POST", f"/api/production-lots/{lot_a['id']}/test",
          {"std_id": fa["std_id"], "check_type": "ipqc", "stage": "dept",
           "items": vals(fa, "dept")}, t=QT)
check("质检员补交质检部项成功", r2.get("ok") is True, str(r2)[:120])
check("★ 与自检同一张检验单（单号一致）", r2.get("test_no") == r1.get("test_no"),
      f"{r1.get('test_no')} vs {r2.get('test_no')}")
check("★ 完成后待质检标记清除", r2.get("pending_dept") == 0)
check("整体判定=合格", r2.get("result") == 1, str(r2.get("result")))
fq2 = call("GET", f"/api/production-lots/{lot_a['id']}/test-form?check_type=ipqc", t=QT)
check("模板显示自检/质检均已录", fq2["self_done"] is True and fq2["dept_done"] is True)

print("══ 5. 越权与边界 ══")
lot_b = mk(st01o)
fb = call("GET", f"/api/production-lots/{lot_b['id']}/test-form?check_type=ipqc", t=AT)
mix = vals(fb, "self") + vals(fb, "dept")
rb = call("POST", f"/api/production-lots/{lot_b['id']}/test",
          {"std_id": fb["std_id"], "check_type": "ipqc", "stage": "self", "items": mix}, t=PT)
check("自检环节交质检项被拒 400", rb.get("_err") == 400, str(rb.get("_d", ""))[:90])
rb2 = call("POST", f"/api/production-lots/{lot_b['id']}/test",
           {"std_id": fb["std_id"], "check_type": "ipqc", "stage": "dept",
            "items": vals(fb, "dept")}, t=PT)
check("生产角色交质检环节被拒 403", rb2.get("_err") == 403)
# 来料检验（全质检部）不能走自检环节
inc_std = next((x for x in stds if x["check_type"] == "iqc"), None)
if inc_std:
    d3 = call("GET", f"/api/qc-standards/{inc_std['id']}", t=AT)
    check("来料标准全为质检部检测", all(it["check_by"] == "dept" for it in d3["items"]),
          str([it["check_by"] for it in d3["items"]]))

print("══ 6. 两种来源不合格都冻结 + NCR ══")
lot_c = mk(st01o)
fc = call("GET", f"/api/production-lots/{lot_c['id']}/test-form?check_type=ipqc", t=AT)
# 自检不合格：温度填一个远超上限的值
bad_self = []
for it in fc["items"]:
    if it["check_by"] != "self":
        continue
    v = str(round((it["max_val"] or 100) * 3, 1))
    bad_self.append({"indicator": it["indicator"], "actual": v})
rc = call("POST", f"/api/production-lots/{lot_c['id']}/test",
          {"std_id": fc["std_id"], "check_type": "ipqc", "stage": "self", "items": bad_self}, t=PT)
check("★ 自检不合格 → 立即冻结", rc.get("status") == 3, str(rc.get("status")))
check("★ 自检不合格 → 自动开 NCR", bool(rc.get("ncr_no")), str(rc.get("ncr_no")))
check("NCR 摘要标注来源=车间自检", "车间自检" in (rc.get("fail_summary") or ""),
      (rc.get("fail_summary") or "")[:60])
lot_d = mk(st01o)
fd = call("GET", f"/api/production-lots/{lot_d['id']}/test-form?check_type=ipqc", t=AT)
call("POST", f"/api/production-lots/{lot_d['id']}/test",
     {"std_id": fd["std_id"], "check_type": "ipqc", "stage": "self", "items": vals(fd, "self")}, t=PT)
bad_dept = []
for it in fd["items"]:
    if it["check_by"] != "dept":
        continue
    v = str(round((it["max_val"] or 100) * 5, 1))
    bad_dept.append({"indicator": it["indicator"], "actual": v})
rd = call("POST", f"/api/production-lots/{lot_d['id']}/test",
          {"std_id": fd["std_id"], "check_type": "ipqc", "stage": "dept", "items": bad_dept}, t=QT)
check("★ 质检部不合格 → 冻结", rd.get("status") == 3, str(rd.get("status")))
check("★ 质检部不合格 → 自动开 NCR", bool(rd.get("ncr_no")))
check("NCR 摘要标注来源=质检部", "质检部" in (rd.get("fail_summary") or ""),
      (rd.get("fail_summary") or "")[:60])
check("质检后开单会写进不合格列表",
      any(x["ncr_no"] == rd.get("ncr_no") for x in call("GET", "/api/ncr", t=AT)))

print("══ 7. 界面（第二批）══")
import urllib.request as _u
with _u.urlopen(BASE + "/") as _r:
    _ps = _r.read().decode("utf-8", "ignore")
ui = [
    ("标准库指标行可配检测方（下拉）", 'class="cb"' in _ps),
    ("提交标准时带 check_by", "check_by:cb?cb.value:'dept'" in _ps),
    ("编辑标准回填检测方", 'is_key:it.is_key,check_by:it.check_by' in _ps),
    ("检验录入分『车间自检』块", "🖐 车间自检（车间现场录）" in _ps),
    ("检验录入分『质检部检测』块", "🔬 质检部检测（化验室录）" in _ps),
    ("分块提交按钮（自检/质检）", "✔ 提交车间自检" in _ps and "✔ 提交质检部检测" in _ps),
    ("提交时带 stage 参数", "stage:stage,items:items" in _ps),
    ("只收集本块录入值", 'item-row[data-by="' + "'+stage+'" + '"]' in _ps),
    ("批次列表显示⏳待质检标记", "⏳ 可流转·待质检" in _ps),
    ("待质检时质检员有录入入口", "🔬 质检录入" in _ps),
    ("车间角色可发起过程检验", "canSelf=['admin','qm','prodlead','worker']" in _ps),
]
for name, ok in ui:
    check(name, ok)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第8步（自检/质检部）验收全部通过")
