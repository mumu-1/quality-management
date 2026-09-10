# -*- coding: utf-8 -*-
"""第4步 e2e 验收：生产批次 + 过程检验 + 成品检验 + COA + 放行控制
覆盖：全链路(原料→溶解→合成→压滤→干燥→包装成品→OQC→COA) / 不合格冻结拦截下工序 /
父批约束(必须上工序合格批) / 建批权限 / 放行控制(未检合格不可发货) / 公开COA页+二维码 / 数据链路追溯
运行: python scripts/e2e_step4.py （先启动服务）
"""
import json, sys, urllib.request, urllib.error

BASE = "http://localhost:8000"


def call(method, path, body=None, token="", raw=False):
    req = urllib.request.Request(BASE + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("token", token)
    try:
        with urllib.request.urlopen(req) as r:
            d = r.read().decode()
            return d if raw else json.loads(d)
    except urllib.error.HTTPError as e:
        return ({"_err": e.code, "_detail": e.read().decode()[:250]} if not raw else "")


PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


def login(u):
    return call("POST", "/api/auth/login", {"username": u, "password": "123456"}).get("token", "")


AT, QT, QCT, PLT, BT, ST = (login(u) for u in
                            ["admin", "qm", "qc", "prodlead", "buyer", "store"])
check("各角色登录", all([AT, QT, QCT, PLT, BT, ST]))

sts = {s["code"]: s for s in call("GET", "/api/station", token=AT)}
eqs = call("GET", "/api/equipment", token=AT)
teams = call("GET", "/api/team", token=AT)
mats = {m["code"]: m for m in call("GET", "/api/material", token=AT)}


def eq_of(code):
    return next(e for e in eqs if e["station_id"] == sts[code]["id"])


def build(station_code, parent_no, qty=8.0, material_id=None, token=None):
    return call("POST", "/api/production-lots", {
        "station_id": sts[station_code]["id"], "equipment_id": eq_of(station_code)["id"],
        "team_id": teams[0]["id"], "material_id": material_id,
        "parent_lot_no": parent_no, "qty": qty}, token=token or PLT)


def test_items(std_id, fail_first=False, token=None):
    """按模板造实测值；fail_first=第一项故意超标"""
    form = call("GET", f"/api/production-lots/{std_id}/test-form?check_type=ipqc", token=token or QCT)
    items = []
    for i, it in enumerate(form.get("items", [])):
        lo, hi = it.get("min_val"), it.get("max_val")
        if i == 0 and fail_first and (lo is not None or hi is not None):
            items.append({"indicator": it["indicator"], "actual": str((lo - 1) if lo is not None else (hi + 1))})
        elif lo is not None and hi is not None:
            items.append({"indicator": it["indicator"], "actual": str(round((lo + hi) / 2, 2))})
        elif lo is not None:
            items.append({"indicator": it["indicator"], "actual": str(lo + 0.5)})
        elif hi is not None:
            items.append({"indicator": it["indicator"], "actual": str(round(hi * 0.5, 2))})
        else:
            items.append({"indicator": it["indicator"], "actual": "合格"})
    return form, items


print("══ 1. 全链路：原料 → 溶解 → 合成 → 压滤 → 干燥 → 包装成品 → OQC → COA ══")
# 1a. 首工序 ST01 建批（父=已合格原料批 RA-SUP01-001）
inp = call("GET", "/api/incoming", token=AT)
raw_ok = next(l for l in inp if l["status"] == 2)                    # 合格放行原料批
p1 = build("ST01", raw_ok["lot_no"], 20.0, mats["RAW-001"]["id"])
check("ST01 建批成功", p1.get("ok") is True, str(p1))
check("批号含工序/设备/父引用", "[RA-" in p1["lot_no"] and "ST01" in p1["lot_no"], p1["lot_no"])
lots = {l["id"]: l for l in call("GET", "/api/production-lots", token=AT)}
check("新批状态=待过程检验", lots[p1["id"]]["status"] == 1, lots[p1["id"]]["status_name"])

# 1b. ST01 过程检验合格
form1, items1 = test_items(p1["id"])
check("过程检验模板按工序带出", form1.get("std_id") and len(form1.get("items", [])) >= 2,
      f"{form1.get('std_name')} {len(form1.get('items',[]))}项")
r1 = call("POST", f"/api/production-lots/{p1['id']}/test",
          {"std_id": form1["std_id"], "check_type": "ipqc", "items": items1}, token=QCT)
check("ST01 过程检验合格", r1.get("result") == 1, str(r1.get("_detail", ""))[:80])
lots = {l["id"]: l for l in call("GET", "/api/production-lots", token=AT)}
check("ST01 批状态=过程合格(可流入下工序)", lots[p1["id"]]["status"] == 2, lots[p1["id"]]["status_name"])

# 1c. 主链逐工序建批+检验：ST03→ST05→ST06
chain = {}
parent_no = p1["lot_no"]
prev_id = p1["id"]
for code in ["ST03", "ST05", "ST06"]:
    # 可用父批里应含上一批
    avail = call("GET", f"/api/production-lots/available-parents?station_id={sts[code]['id']}", token=PLT)
    check(f"{code} 可选父批含上工序合格批", any(x["lot_no"] == parent_no for x in avail), f"{len(avail)} 个候选")
    b = build(code, parent_no, 18.0)
    check(f"{code} 建批成功", b.get("ok") is True, str(b.get("lot_no")))
    fm, it = test_items(b["id"])
    rr = call("POST", f"/api/production-lots/{b['id']}/test",
              {"std_id": fm["std_id"], "check_type": "ipqc", "items": it}, token=QCT)
    check(f"{code} 过程检验合格", rr.get("result") == 1, str(rr)[:70])
    chain[code] = b
    parent_no = b["lot_no"]
    prev_id = b["id"]

print("══ 2. 成品批：过程检验 → 待成品检验 → OQC → COA ══")
fg_b = build("ST08", chain["ST06"]["lot_no"], 16.0, mats["FG-001"]["id"])
check("ST08 成品牌建批(挂成品物料)", fg_b.get("ok") is True, str(fg_b.get("lot_no")))
lots = {l["id"]: l for l in call("GET", "/api/production-lots", token=AT)}
check("成品牌初始=待过程检验", lots[fg_b["id"]]["status"] == 1)
fm, it = test_items(fg_b["id"])
rr = call("POST", f"/api/production-lots/{fg_b['id']}/test",
          {"std_id": fm["std_id"], "check_type": "ipqc", "items": it}, token=QCT)
check("成品牌过程检验合格", rr.get("result") == 1, str(rr)[:70])
lots = {l["id"]: l for l in call("GET", "/api/production-lots", token=AT)}
check("成品批过程合格→待成品检验", lots[fg_b["id"]]["status"] == 4, lots[fg_b["id"]]["status_name"])
# 放行控制：未做 OQC 时不在成品可用列表
fg_before = call("GET", "/api/fg-available", token=AT)
check("未检合格成品不在放行列表", all(f["id"] != fg_b["id"] for f in fg_before))
# OQC 表单（按成品物料标准）
of = call("GET", f"/api/production-lots/{fg_b['id']}/test-form?check_type=oqc", token=QCT)
check("OQC 模板按成品物料带出", of.get("std_id") and "电池级磷酸铁" in of.get("std_name", ""),
      f"{of.get('std_name')} {len(of.get('items',[]))}项")
oitems = []
for it2 in of["items"]:
    lo, hi = it2.get("min_val"), it2.get("max_val")
    if lo is not None and hi is not None:
        oitems.append({"indicator": it2["indicator"], "actual": str(round((lo + hi) / 2, 2))})
    elif lo is not None:
        oitems.append({"indicator": it2["indicator"], "actual": str(lo + 0.5)})
    elif hi is not None:
        oitems.append({"indicator": it2["indicator"], "actual": str(round(hi * 0.4, 2))})
    else:
        oitems.append({"indicator": it2["indicator"], "actual": "白色粉末无结块"})
roqc = call("POST", f"/api/production-lots/{fg_b['id']}/test",
            {"std_id": of["std_id"], "check_type": "oqc", "items": oitems,
             "customer_id": None}, token=QCT)
check("成品检验合格提交", roqc.get("result") == 1, str(roqc)[:70])
lots = {l["id"]: l for l in call("GET", "/api/production-lots", token=AT)}
check("成品批状态=成品合格", lots[fg_b["id"]]["status"] == 5, lots[fg_b["id"]]["status_name"])
coas = call("GET", "/api/coas", token=AT)
mycoa = next((c for c in coas if c["prod_id"] == fg_b["id"]), None)
check("自动生成 COA", mycoa is not None, mycoa["coa_no"] if mycoa else "")
cd = call("GET", f"/api/coas/{mycoa['id']}", token=AT) if mycoa else {}
check("COA 明细快照带判定", len(cd.get("items", [])) >= 5 and all("pass" in i for i in cd.get("items", [])),
      f"{len(cd.get('items',[]))} 项")
check("COA 产品/批号正确", cd.get("material_name") == "电池级磷酸铁" and cd.get("lot_no") == fg_b["lot_no"])
fg_after = call("GET", "/api/fg-available", token=AT)
check("成品合格批进入放行列表", any(f["id"] == fg_b["id"] for f in fg_after))

print("══ 3. 公开 COA 页 + 二维码（客户扫码：需链接口令，方案A 客户隔离）══")
if mycoa:
    cl = call("GET", "/api/coas", token=AT)
    row = next((x for x in cl if x["coa_no"] == mycoa["coa_no"]), {})
    ck = row.get("access_key", "")
    check("COA 列表带客户链接口令", bool(ck), f"{ck[:8]}…" if ck else "空")
    nokey = call("GET", f"/coa/{mycoa['coa_no']}", raw=True)
    check("★ 不带口令访问被拒（客户隔离）",
          mycoa["coa_no"] not in nokey or "链接无效" in nokey, f"{len(nokey)} 字符")
    html = call("GET", f"/coa/{mycoa['coa_no']}?k={ck}", raw=True)
    check("带口令公开页可访问且含报告编号", mycoa["coa_no"] in html and "检验项目" in html, f"{len(html)} 字符")
    qr0 = call("GET", f"/api/public/coa/{mycoa['coa_no']}/qr.svg", raw=True)
    check("★ 二维码无口令被拒", not ("svg" in qr0[:200].lower() and len(qr0) > 500), f"{len(qr0)} 字节")
    qr = call("GET", f"/api/public/coa/{mycoa['coa_no']}/qr.svg?k={ck}", raw=True)
    check("带口令二维码 SVG 可生成", "svg" in qr[:200].lower() and len(qr) > 500, f"{len(qr)} 字节")

print("══ 4. 不合格冻结 → 禁止流入下工序（拦截验证）══")
bad_b = build("ST03", p1["lot_no"], 10.0)
fm, it = test_items(bad_b["id"], fail_first=True)
rb = call("POST", f"/api/production-lots/{bad_b['id']}/test",
          {"std_id": fm["std_id"], "check_type": "ipqc", "items": it}, token=QCT)
check("故意不合格 → result=2", rb.get("result") == 2, str(rb.get("fail_summary", ""))[:80])
check("自动生成 NCR(生产批)", bool(rb.get("ncr_no")), str(rb.get("ncr_no")))
lots = {l["id"]: l for l in call("GET", "/api/production-lots", token=AT)}
check("不合格批冻结", lots[bad_b["id"]]["status"] == 3, lots[bad_b["id"]]["status_name"])
# 关键拦截：下工序 ST05 想引用冻结批 → 拒绝
blocked = build("ST05", bad_b["lot_no"], 5.0)
check("下工序引用冻结批被拒", blocked.get("_err") == 400, str(blocked.get("_detail", ""))[:60])
# 冻结批不能选为父批候选
avail5 = call("GET", f"/api/production-lots/available-parents?station_id={sts['ST05']['id']}", token=PLT)
check("冻结批不出现在父批候选", all(x["lot_no"] != bad_b["lot_no"] for x in avail5))
# 冻结批不能再提交过程检验
again = call("POST", f"/api/production-lots/{bad_b['id']}/test",
             {"std_id": fm["std_id"], "check_type": "ipqc", "items": it}, token=QCT)
check("冻结批不能再检验", again.get("_err") == 400)
# NCR 列表含该生产批 NCR
ncrs = call("GET", "/api/ncr", token=AT)
pncr = next((n for n in ncrs if n.get("ncr_no") == rb.get("ncr_no")), None)
check("NCR 列表可见且来源=生产批", pncr and pncr.get("source") == "production",
      (pncr or {}).get("material_name", ""))

print("══ 5. 父批约束（防错）══")
# 首工序用"待取样"原料批 → 拒绝
wait_raw = next((l for l in call("GET", "/api/incoming", token=AT) if l["status"] == 0), None)
if wait_raw:
    r = build("ST01", wait_raw["lot_no"], 5.0)
    check("首工序用未检合格原料批被拒", r.get("_err") == 400, str(r.get("_detail", ""))[:50])
# 跨工序：ST05 直接引用 ST01 批（跳过 ST03）→ 拒绝
cross = build("ST05", p1["lot_no"], 5.0)
check("跳过工序引用父批被拒", cross.get("_err") == 400, str(cross.get("_detail", ""))[:60])
# 不存在的父批
nope = build("ST03", "20260101-ST99-EQ99-999-[X]-甲", 5.0)
check("父批不存在被拒", nope.get("_err") == 400)

print("══ 6. 建批权限 ══")
r_qc = build("ST03", p1["lot_no"], 5.0, token=QCT)
check("检验员不能建批 403", r_qc.get("_err") == 403)
r_buyer = build("ST03", p1["lot_no"], 5.0, token=BT)
check("采购不能建批 403", r_buyer.get("_err") == 403)
r_store = build("ST03", p1["lot_no"], 5.0, token=ST)
check("仓储不能建批 403", r_store.get("_err") == 403)
r_admin = build("ST03", p1["lot_no"], 4.0, token=AT)
check("管理员可建批", r_admin.get("ok") is True)

print("══ 7. 数据链路追溯（成品→原料逐级可查）══")
import urllib.parse
cur_no, hops, root_raw = fg_b["lot_no"], 0, None
while hops < 10:
    rows = call("GET", "/api/production-lots?keyword=" + urllib.parse.quote(cur_no), token=AT)
    row = next((x for x in rows if x["lot_no"] == cur_no), None)
    if not row:
        break
    if row["parent_type"] == "incoming":
        root_raw = row["parent_lot_no"]      # 首工序批的父批=原料批
        break
    cur_no = row["parent_lot_no"]
    hops += 1
check("成品批可逐级追溯到首工序", hops >= 4, f"向上追溯 {hops} 层，首工序父批(原料): {root_raw or '?'}")
final_in = call("GET", "/api/incoming" if not root_raw else
                "/api/incoming?keyword=" + urllib.parse.quote(root_raw), token=AT)
hit = [x for x in (final_in or []) if x.get("lot_no") == root_raw] if root_raw else []
check("链路终点为合格原料批(放行/让步)", len(hit) == 1 and hit[0]["status"] in (2, 4),
      f"{(hit[0]['lot_no'] + ' ' + hit[0]['status_name']) if hit else '未找到'}")

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第4步验收全部通过")
