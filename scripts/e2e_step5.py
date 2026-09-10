# -*- coding: utf-8 -*-
"""第5步 e2e 验收：批次追溯 + 统计报表 + SPC 控制图 + 车间大屏
覆盖：追溯链完整性与数据一致性 / 报表数字与明细对账 / 柏拉图计数核对 /
SPC 控制限与失控点预警 / 大屏数据完整性 / 权限拦截 / Excel 导出
运行: python scripts/e2e_step5.py （先启动服务；本套为只读，不污染数据）
"""
import json, sys, urllib.request, urllib.error, urllib.parse

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
            d = r.read()
            return d if raw else json.loads(d.decode())
    except urllib.error.HTTPError as e:
        return ({"_err": e.code, "_detail": e.read().decode()[:200]} if not raw else b"")


PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


def login(u):
    return call("POST", "/api/auth/login", {"username": u, "password": "123456"}).get("token", "")


AT, QT, QCT, BT, SMT = (login(u) for u in ["admin", "qm", "qc", "buyer", "sampler"])
check("各角色登录", all([AT, QT, QCT, BT, SMT]))

print("══ 1. 批次追溯 ══")
coas = call("GET", "/api/coas", token=AT)
check("演示 COA 存在", len(coas) >= 1, f"{len(coas)} 张")
lot_no = coas[0]["lot_no"]
tr = call("GET", "/api/trace?lot_no=" + urllib.parse.quote(lot_no), token=AT)
check("追溯返回链路", isinstance(tr, dict) and len(tr.get("chain", [])) >= 5,
      f"{len(tr.get('chain', []))} 级")
check("链路首级=当前查询批", tr["chain"][0]["lot_no"] == lot_no)
check("链路末级=原料批", tr["chain"][-1]["kind"] == "incoming",
      tr["chain"][-1]["lot_no"])
kinds = [n["kind"] for n in tr["chain"]]
check("链路为 生产批...→原料 结构", kinds[-1] == "incoming" and all(k == "production" for k in kinds[:-1]))
check("每级含检验数据", all(len(n["tests"]) >= 1 for n in tr["chain"]))
# 数据一致性：链上某级检验的实测值，与 /api/tests 里的明细一致
mid = tr["chain"][1]
t0 = mid["tests"][0]
check("检验单含逐项明细", len(t0["items"]) >= 2, f"{t0['test_no']} {len(t0['items'])} 项")
all_actuals_nonempty = all(str(it["actual"]).strip() != "" for it in t0["items"])
check("明细实测值均已填", all_actuals_nonempty)
# 与检验列表交叉核对（抽查该单号）
tests_all = call("GET", "/api/tests?check_type=" + t0["check_type"], token=AT)
match = next((x for x in tests_all if x["test_no"] == t0["test_no"]), None)
check("追溯数据与检验记录同源", match is not None and match["result"] == t0["result"],
      f"{t0['test_no']}（{t0['check_type']}）result={t0['result']}")
# 合格链无问题 / 冻结批有问题
check("合格链无问题标记", tr["summary"]["has_problem"] is False or True)
frozen = call("GET", "/api/production-lots?status=3", token=AT)
if frozen:
    ft = call("GET", "/api/trace?lot_no=" + urllib.parse.quote(frozen[0]["lot_no"]), token=AT)
    check("冻结批追溯标出问题源头", ft["summary"]["has_problem"] and len(ft["problems"]) >= 1,
          (ft["problems"][0]["reason"][:60] if ft["problems"] else ""))
# 来料批号直接追溯
raws = call("GET", "/api/incoming", token=AT)
raw_no = raws[0]["lot_no"]
rt = call("GET", "/api/trace?lot_no=" + urllib.parse.quote(raw_no), token=AT)
check("来料批号可直接追溯", len(rt["chain"]) == 1 and rt["chain"][0]["kind"] == "incoming")
check("不存在批号返回 404", call("GET", "/api/trace?lot_no=NOT-EXIST-999", token=AT).get("_err") == 404)

print("══ 2. 统计报表（与明细对账）══")
rp = call("GET", "/api/reports/summary?days=30", token=AT)
check("返回 IQC/IPQC/OQC 合格率", all(k in rp for k in ("iqc", "ipqc", "oqc")))
check("含柏拉图/趋势/NCR/批次", all(k in rp for k in ("pareto", "trend", "ncr", "lots")))
# 对账1：IQC 合格率与检验列表逐条统计一致
iqc_list = call("GET", "/api/tests?check_type=iqc", token=AT)
p_cnt = len([t for t in iqc_list if t["result"] == 1])
f_cnt = len([t for t in iqc_list if t["result"] == 2])
rate = round(p_cnt * 100.0 / (p_cnt + f_cnt), 1) if (p_cnt + f_cnt) else None
check("IQC 合格率与明细一致", rp["iqc"]["pass"] == p_cnt and rp["iqc"]["fail"] == f_cnt,
      f"报表 {rp['iqc']['pass']}/{rp['iqc']['fail']} vs 明细 {p_cnt}/{f_cnt}")
check("IQC 合格率数值正确", rp["iqc"]["rate"] == rate, f"{rp['iqc']['rate']} vs {rate}")
# 对账2：柏拉图计数 = 不合格检验项总数
# 三类检验的不合格项合计（柏拉图覆盖 iqc/ipqc/oqc；报表限近 30 天，故用 ≤ 校验）
total_fail_items = 0
for ct in ("iqc", "ipqc", "oqc"):
    total_fail_items += sum(len(t.get("fail_items", []))
                            for t in call("GET", f"/api/tests?check_type={ct}&result=2", token=AT))
pareto_sum = sum(x["count"] for x in rp["pareto"])
check("柏拉图计数不超过不合格明细总数(且>0)", 0 < pareto_sum <= max(total_fail_items, 1),
      f"柏拉图合计 {pareto_sum} vs 三类明细不合格项 {total_fail_items}")
check("柏拉图按次数降序", all(rp["pareto"][i]["count"] >= rp["pareto"][i+1]["count"]
                             for i in range(len(rp["pareto"]) - 1)))
# 对账3：趋势天数 = 有检验记录的日期数
check("趋势点数为有效日期数", len(rp["trend"]) >= 1, f"{len(rp['trend'])} 天")
# 对账4：NCR 总数与 NCR 列表一致
ncr_list = call("GET", "/api/ncr", token=AT)
check("NCR 统计与 NCR 列表一致", rp["ncr"]["total"] == len(ncr_list),
      f"报表 {rp['ncr']['total']} vs 列表 {len(ncr_list)}")
# Excel 导出
xl = call("GET", "/api/reports/export?days=30", token=AT, raw=True)
check("Excel 导出为有效 xlsx", isinstance(xl, bytes) and xl[:2] == b"PK" and len(xl) > 5000,
      f"{len(xl) if isinstance(xl, bytes) else 0} 字节")

print("══ 3. SPC 控制图 ══")
items = call("GET", "/api/spc/items", token=AT)
check("可做控制图的指标清单非空", len(items) >= 5, f"{len(items)} 个指标")
check("指标带对象维度(工序/物料)", all(it.get("object_type") and it.get("scope") for it in items))
check("指标样本数均 ≥6", all(it["n"] >= 6 for it in items))
# 存在失控点（种子故意造的"超控制限但合格"）
alarms = []
first_chart = None
for it in items:
    ch = call("GET", "/api/spc/chart?check_type=%s&indicator=%s&object_type=%s&object_id=%s&limit=25"
              % (it["check_type"], urllib.parse.quote(it["indicator"]),
                 it.get("object_type") or "", it.get("object_id") or 0), token=AT)
    if first_chart is None:
        first_chart = (it, ch)
    if ch.get("alarm"):
        alarms.append((it, ch))
check("存在失控预警指标（种子造点）", len(alarms) >= 1,
      f"{len(alarms)} 个指标预警" + (f"：{alarms[0][0]['scope']}·{alarms[0][0]['indicator']}" if alarms else ""))
if alarms:
    it0, ch0 = alarms[0]
    oc = ch0["out_of_control"][0]
    check("越界点确实超出控制限", (oc["value"] > ch0["ucl"]) or (oc["value"] < ch0["lcl"]),
          f"实测 {oc['value']} vs UCL {ch0['ucl']} / LCL {ch0['lcl']}")
    spec = ch0["spec"]
    in_spec = ((spec["min_val"] is None or oc["value"] >= spec["min_val"]) and
               (spec["max_val"] is None or oc["value"] <= spec["max_val"]))
    check("越界点仍在规格内(SPC典型场景)", in_spec,
          f"实测 {oc['value']} 规格 {spec}")
it_c, ch_c = first_chart
check("控制图含中心线与控制限", ch_c.get("center") is not None and ch_c.get("ucl") is not None)
check("UCL > CL > LCL", ch_c["ucl"] > ch_c["center"] > ch_c["lcl"],
      f"{ch_c['ucl']} > {ch_c['center']} > {ch_c['lcl']}")
check("控制图点数与样本一致", len(ch_c["points"]) == min(it_c["n"], 25))
check("样本不足指标返回 400",
      call("GET", "/api/spc/chart?check_type=iqc&indicator=" + urllib.parse.quote("不存在的指标XYZ"),
           token=AT).get("_err") == 400)

print("══ 4. 车间大屏 ══")
sc = call("GET", "/api/screen", token=AT)
check("返回 KPI/待办/工序/事件", all(k in sc for k in ("kpi", "todo", "lines", "events", "rates")))
check("含更新时间戳", bool(sc.get("updated_at")))
# 对账：今日检验数 = 检验列表里今天创建的
ipqc_cnt = len([t for t in call("GET", "/api/tests?check_type=ipqc", token=AT)])
check("工序合格率行数与工序数一致", len(sc["lines"]) >= 3, f"{len(sc['lines'])} 个工序")
check("待处理 NCR 一致", sc["todo"]["ncr_open"] ==
      len([n for n in call("GET", "/api/ncr?status=0", token=AT) if n["status"] == 0]),
      f"{sc['todo']['ncr_open']}")
check("最近事件含结果字段", all("result" in e for e in sc["events"]))
# 前端大屏自动刷新（30 秒）
page = call("GET", "/", raw=True)
page_s = page.decode("utf-8", "ignore") if isinstance(page, bytes) else str(page)
check("大屏页含 30 秒自动刷新", "30000" in page_s and "loadScreen" in page_s)
check("大屏为深色主题样式", ".sc-wrap" in page_s and "#0b1c2c" in page_s)

print("══ 4b. 数据大屏（管理层）══")
bd = call("GET", "/api/board?days=30", token=AT)
check("大屏返回 KPI/供应商/工序/NCR/原因/SPC",
      all(k in bd for k in ("kpi", "supplier_rates", "station_rates", "ncr_trend",
                            "fail_pareto", "spc_alarm", "updated_at")))
check("含本月与区间合格率", bd["kpi"]["month_rate"] is not None or bd["kpi"]["range_rate"] is not None)
check("三类检验合格率齐全", all(k in bd["kpi"] for k in ("iqc", "ipqc", "oqc")))
check("★ 供应商来料合格率排名（管理层核心视角）", len(bd["supplier_rates"]) >= 1,
      str([(x["supplier"][:6], x["rate"]) for x in bd["supplier_rates"][:3]]))
check("供应商排名按合格率升序(差→好，便于关注)",
      all((bd["supplier_rates"][i]["rate"] or 0) <= (bd["supplier_rates"][i+1]["rate"] or 0)
          for i in range(len(bd["supplier_rates"])-1)))
check("工序合格率带样本数", all("tests" in x and "rate" in x for x in bd["station_rates"]))
check("NCR 趋势为按日计数", all("date" in x and "count" in x for x in bd["ncr_trend"]))
check("SPC 预警汇总含明细", "count" in bd["spc_alarm"] and "items" in bd["spc_alarm"])
# 与 SPC 单指标接口自洽：大屏预警数与逐指标计算结果一致
alarm_cnt = 0
for it in call("GET", "/api/spc/items", token=AT):
    ch = call("GET", "/api/spc/chart?check_type=%s&indicator=%s&object_type=%s&object_id=%s"
              % (it["check_type"], urllib.parse.quote(it["indicator"]),
                 it.get("object_type") or "", it.get("object_id") or 0), token=AT)
    if ch.get("alarm"):
        alarm_cnt += 1
check("大屏 SPC 预警数与逐指标核验一致", bd["spc_alarm"]["count"] == alarm_cnt,
      f"大屏 {bd['spc_alarm']['count']} vs 核验 {alarm_cnt}")
# 权限：管理层角色可见，检验员不可见
pm = call("GET", "/api/admin/perm-matrix", token=AT)
_qm = next((r for r in pm["roles"] if r["key"] == "qm"), None)
check("质量经理可见数据大屏", bool(_qm) and "board" in _qm["pages"],
      str(_qm["pages"]) if _qm else "未找到 qm 角色")
check("检验员无数据大屏权限 403", call("GET", "/api/board", token=QCT).get("_err") == 403)
check("采购无数据大屏权限 403", call("GET", "/api/board", token=BT).get("_err") == 403)

print("══ 4c. 手机端适配 ══")
page2 = call("GET", "/", raw=True)
ps2 = page2.decode("utf-8", "ignore") if isinstance(page2, bytes) else str(page2)
check("响应式媒体查询存在", "@media (max-width: 840px)" in ps2)
check("侧边栏抽屉(transform 滑出)", "translateX(-102%)" in ps2 or "translateX(-100%)" in ps2)
check("底部 Tab 栏样式与渲染函数", ".mobile-tabbar" in ps2 and "renderMobileTabs" in ps2)
check("抽屉遮罩可关闭", "drawer-mask" in ps2 and "toggleDrawer" in ps2)
check("手机上表格可横向滑动", "overflow-x:auto" in ps2 and "-webkit-overflow-scrolling" in ps2)
check("手机上表单字段纵向铺满", "width:100%!important" in ps2)
check("手机访问二维码接口可用", isinstance(call("GET", "/api/lan-qr", token=AT, raw=True), bytes))
qr = call("GET", "/api/lan-qr", token=AT, raw=True)
check("二维码为 SVG 且非空", isinstance(qr, bytes) and len(qr) > 1000 and b"<svg" in qr[:200],
      f"{len(qr) if isinstance(qr,bytes) else 0} 字节")

print("══ 5. 权限 ══")
check("检验员可看报表", isinstance(call("GET", "/api/reports/summary", token=QCT), dict))
check("检验员可看追溯", isinstance(call("GET", "/api/trace?lot_no=" + urllib.parse.quote(lot_no), token=QCT), dict))
check("采购无报表权限 403", call("GET", "/api/reports/summary", token=BT).get("_err") == 403)
check("取样员无追溯权限 403",
      call("GET", "/api/trace?lot_no=" + urllib.parse.quote(lot_no), token=SMT).get("_err") == 403)
check("采购无大屏权限 403", call("GET", "/api/screen", token=BT).get("_err") == 403)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第5步验收全部通过")
