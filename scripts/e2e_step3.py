# -*- coding: utf-8 -*-
"""第3步 e2e 验收：来料检验闭环
覆盖：到货登记→批号生成 / 取样 / 检验提交自动判定 /
合格放行 / 故意不合格→冻结+NCR / 处置权限(让步需质量经理) / 越权拦截
运行: python scripts/e2e_step3.py （先启动服务）
"""
import json, sys, urllib.request, urllib.error

BASE = "http://localhost:8000"


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
        return {"_err": e.code, "_detail": e.read().decode()[:250]}


PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


def login(u):
    return call("POST", "/api/auth/login", {"username": u, "password": "123456"}).get("token", "")


AT, QT, QCT, BT, ST, SMT = (login(u) for u in
                            ["admin", "qm", "qc", "buyer", "store", "sampler"])
check("各角色登录", all([AT, QT, QCT, BT, ST, SMT]))

print("══ 1. 到货登记 + 批号生成 ══")
# 物料/供应商
mats = call("GET", "/api/material", token=AT)
sup1 = next(s["id"] for s in call("GET", "/api/supplier", token=AT) if s["code"] == "SUP-001")
raw1 = next(m["id"] for m in mats if m["code"] == "RAW-001")
raw4 = next(m["id"] for m in mats if m["code"] == "RAW-004")   # 双氧水：尚无来料
# 采购登记到货（双氧水，之前没有该供应商批号）
r1 = call("POST", "/api/incoming", {"material_id": raw4, "supplier_id": sup1,
                                    "supplier_lot": "TEST-B1", "qty": 12.5, "vehicle": "测A001"}, token=BT)
check("采购登记到货成功且生成批号", r1.get("ok") is True and r1.get("lot_no","").startswith("2"),
      str(r1.get("lot_no", "")))
lot1_id, lot1_no = r1["id"], r1["lot_no"]
check("批号格式 日期-RA-SUP01-NNN", len(lot1_no.split("-")) == 4 and lot1_no.split("-")[2] == "SUP01", lot1_no)
# 到货登记状态=待取样
lst = call("GET", "/api/incoming", token=AT)
row = next(x for x in lst if x["id"] == lot1_id)
check("登记后状态=待取样", row["status"] == 0)
# 角色校验：检验员不能登记到货
over = call("POST", "/api/incoming", {"material_id": raw1, "supplier_id": sup1,
                                      "supplier_lot": "X", "qty": 1}, token=QCT)
check("检验员登记到货被拒 403", over.get("_err") == 403)
# 仓储也不能登记
over2 = call("POST", "/api/incoming", {"material_id": raw1, "supplier_id": sup1,
                                       "supplier_lot": "X", "qty": 1}, token=ST)
check("仓储登记到货被拒 403", over2.get("_err") == 403)

print("══ 2. 取样 ══")
s1 = call("POST", f"/api/incoming/{lot1_id}/sample", {"sample_qty": "500g"}, token=SMT)
check("取样员取样成功", s1.get("ok") is True and s1.get("sample_no", "").startswith("SP-"),
      str(s1.get("sample_no", "")))
row2 = next(x for x in call("GET", "/api/incoming", token=AT) if x["id"] == lot1_id)
check("取样后状态=待检验", row2["status"] == 1)
# 重复取样被拒
s2 = call("POST", f"/api/incoming/{lot1_id}/sample", {"sample_qty": "500g"}, token=SMT)
check("已取样批次不能重复取样", s2.get("_err") == 400)
# 采购不能取样（需要取样员/质检）
s3 = call("POST", f"/api/incoming/{lot1_id}/sample", {"sample_qty": "x"}, token=BT)
check("采购取样被拒 403", s3.get("_err") == 403)

print("══ 3. 检验表单自动带出标准 ══")
form = call("GET", f"/api/incoming/{lot1_id}/test-form", token=QCT)
check("检验员可取到标准模板", form.get("std_id") and form.get("items"), str(form.get("std_no")))
check("双氧水模板带出4项", len(form.get("items", [])) == 4, f"{len(form.get('items',[]))} 项")
h2o2 = next(it for it in form["items"] if "过氧化氢" in it["indicator"])
check("关键项限值正确", h2o2["min_val"] == 27.5, str(h2o2))

print("══ 4. 合格链路：提交 → 放行 ══")
pass_items = [{"indicator": it["indicator"], "actual": "28.5" if "过氧化氢" in it["indicator"]
               else ("0.02" if "游离酸" in it["indicator"]
                     else ("0.05" if "不挥发物" in it["indicator"] else "97.5"))}
              for it in form["items"]]
t1 = call("POST", f"/api/incoming/{lot1_id}/test", {"std_id": form["std_id"], "items": pass_items}, token=QCT)
check("提交检验返回合格", t1.get("result") == 1, str(t1.get("_detail", "")))
row3 = next(x for x in call("GET", "/api/incoming", token=AT) if x["id"] == lot1_id)
check("合格批次状态=放行", row3["status"] == 2, row3["status_name"])
tl = call("GET", "/api/tests", token=AT)
check("检验记录含该单且合格", any(t["test_no"] == t1["test_no"] and t["result_name"] == "合格" for t in tl))

print("══ 5. 故意不合格：自动冻结 + 生成 NCR ══")
# 再登记一批双氧水（同供应商同天 → 批号序号应+1）
r2 = call("POST", "/api/incoming", {"material_id": raw4, "supplier_id": sup1,
                                    "supplier_lot": "TEST-B2", "qty": 5}, token=BT)
lot2_id, lot2_no = r2["id"], r2["lot_no"]
call("POST", f"/api/incoming/{lot2_id}/sample", {"sample_qty": "300g"}, token=SMT)
form2 = call("GET", f"/api/incoming/{lot2_id}/test-form", token=QCT)
bad_items = [{"indicator": it["indicator"], "actual": "26.0" if "过氧化氢" in it["indicator"]
              else "0.05"} for it in form2["items"]]
t2 = call("POST", f"/api/incoming/{lot2_id}/test", {"std_id": form2["std_id"], "items": bad_items}, token=QCT)
check("不合格判定返回 result=2", t2.get("result") == 2, str(t2))
check("不合格自动生成 NCR 号", bool(t2.get("ncr_no")), str(t2.get("ncr_no")))
check("fail_summary 含超限描述", "实测26.0" in (t2.get("fail_summary") or ""), str(t2.get("fail_summary")))
row4 = next(x for x in call("GET", "/api/incoming", token=AT) if x["id"] == lot2_id)
check("不合格批次状态=冻结", row4["status"] == 3, row4["status_name"])
check("来料列表可见 NCR 关联", row4.get("ncr_no") == t2.get("ncr_no"))
# 冻结批次不能再检验
t3 = call("POST", f"/api/incoming/{lot2_id}/test", {"std_id": form2["std_id"], "items": pass_items}, token=QCT)
check("冻结批次不能再提交检验", t3.get("_err") == 400)

print("══ 6. NCR 处置 + 权限 ══")
ncrs = call("GET", "/api/ncr", token=AT)
ncr = next(n for n in ncrs if n["ncr_no"] == t2["ncr_no"])
check("NCR 状态=待处理", ncr["status"] == 0)
# 检验员不能处置
d0 = call("POST", f"/api/ncr/{ncr['id']}/dispose", {"disposition": "waive", "remark": "自批"}, token=QCT)
check("检验员让步被拒 403（不能自己放行）", d0.get("_err") == 403)
# 仓储不能处置
d0b = call("POST", f"/api/ncr/{ncr['id']}/dispose", {"disposition": "reject", "remark": "x"}, token=ST)
check("仓储处置被拒 403", d0b.get("_err") == 403)
# 采购可以拒收退货
d1 = call("POST", f"/api/ncr/{ncr['id']}/dispose", {"disposition": "reject", "remark": "通知供应商退货"}, token=BT)
check("采购拒收退货成功", d1.get("ok") is True and d1.get("ncr_status") == 1)
row5 = next(x for x in call("GET", "/api/incoming", token=AT) if x["id"] == lot2_id)
check("拒收后批次状态=拒收退货", row5["status"] == 5)
# 重复处置被拒
d2 = call("POST", f"/api/ncr/{ncr['id']}/dispose", {"disposition": "waive"}, token=QT)
check("已处置 NCR 不能重复处置", d2.get("_err") == 400)
# 让步接收：另造一批不合格 → 质量经理 waive
r3 = call("POST", "/api/incoming", {"material_id": raw4, "supplier_id": sup1,
                                    "supplier_lot": "TEST-B3", "qty": 3}, token=BT)
lot3_id = r3["id"]
call("POST", f"/api/incoming/{lot3_id}/sample", {"sample_qty": "300g"}, token=SMT)
form3 = call("GET", f"/api/incoming/{lot3_id}/test-form", token=QCT)
t4 = call("POST", f"/api/incoming/{lot3_id}/test", {"std_id": form3["std_id"],
                                                    "items": [{"indicator": "过氧化氢含量(H2O2)", "actual": "26.5"}]}, token=QCT)
ncr2 = next(n for n in call("GET", "/api/ncr", token=AT) if n["ncr_no"] == t4["ncr_no"])
# 采购不能让步（需质量经理）
d3 = call("POST", f"/api/ncr/{ncr2['id']}/dispose", {"disposition": "waive", "remark": "想放行"}, token=BT)
check("采购让步被拒 403（须质量经理）", d3.get("_err") == 403)
d4 = call("POST", f"/api/ncr/{ncr2['id']}/dispose", {"disposition": "waive", "remark": "偏差轻微特批"}, token=QT)
check("质量经理让步接收成功", d4.get("ok") is True and d4.get("lot_status") == 4)
row6 = next(x for x in call("GET", "/api/incoming", token=AT) if x["id"] == lot3_id)
check("让步后批次状态=让步接收(可用)", row6["status"] == 4)

print("══ 7. 历史检验/不合格明细 ══")
detail = call("GET", f"/api/tests/{t2['id'] if 'id' in t2 else 0}", token=AT)
# 从列表拿 id
tl2 = call("GET", "/api/tests?result=2", token=AT)
bad_test = next((t for t in tl2 if t["test_no"] == t2["test_no"]), None)
check("不合格检验在列表可见且带 fail_items", bad_test and len(bad_test.get("fail_items", [])) >= 1,
      str(bad_test.get("fail_items") if bad_test else None))
detail = call("GET", f"/api/tests/{bad_test['id']}", token=AT) if bad_test else {}
check("检验详情含逐项实测+判定", len(detail.get("items", [])) >= 4 and all("pass" in it for it in detail.get("items", [])))
# NCR 筛选
ncr_wait = call("GET", "/api/ncr?status=0", token=AT)
check("NCR 状态筛选只剩待处理", all(n["status"] == 0 for n in ncr_wait))

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第3步验收全部通过")
