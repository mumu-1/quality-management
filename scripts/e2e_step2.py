# -*- coding: utf-8 -*-
"""第2步 e2e 验收：检验标准库
运行: python scripts/e2e_step2.py  （先启动服务）
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
        return {"_err": e.code, "_detail": e.read().decode()[:200]}


PASS, FAIL = [], []


def check(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    print(("  ✔ " if cond else "  ✘ ") + name + ("  " + extra if extra else ""))


# 登录
adm = call("POST", "/api/auth/login", {"username": "admin", "password": "123456"})
AT = adm.get("token", "")
qm = call("POST", "/api/auth/login", {"username": "qm", "password": "123456"})
QT = qm.get("token", "")
qc = call("POST", "/api/auth/login", {"username": "qc", "password": "123456"})
QCT = qc.get("token", "")

print("══ 1. 标准列表与筛选 ══")
lst = call("GET", "/api/qc-standards", token=AT)
check("共 12 套演示标准", len(lst) == 12, f"实际 {len(lst)}")
iqc = [s for s in lst if s["check_type"] == "iqc"]
ipqc = [s for s in lst if s["check_type"] == "ipqc"]
check("来料 7 套 / 过程 5 套", len(iqc) == 7 and len(ipqc) == 5)
check("标准名含物料真名", any("七水硫酸亚铁" in s["name"] for s in iqc))
check("item_count 正确", all(s["item_count"] > 0 for s in lst))
f1 = call("GET", "/api/qc-standards?check_type=iqc", token=AT)
check("按类型筛选只回来料", len(f1) == 7 and all(s["check_type"] == "iqc" for s in f1))
f2 = call("GET", "/api/qc-standards?keyword=" + urllib.parse.quote("磷酸"), token=AT)
check("关键字'磷酸'命中≥2", len(f2) >= 2, f"{len(f2)} 条")
# 只读角色看不到管理入口（后端校验放行读取）
ro = call("GET", "/api/qc-standards", token=QCT)
check("检验员可读标准(只读角色)", isinstance(ro, list) and len(ro) == 12)

print("══ 2. 新建标准 + 防重 ══")
# qc 越权
over = call("POST", "/api/qc-standards", {"object_type": "material", "object_id": 1,
                                          "check_type": "iqc", "items": [{"indicator": "x"}]}, token=QCT)
check("检验员新建被拒 403", over.get("_err") == 403)
# 重复对象×类型
dup = call("POST", "/api/qc-standards", {"object_type": "material", "object_id": 1,
                                         "check_type": "iqc",
                                         "items": [{"indicator": "水分", "max_val": 1}]}, token=AT)
check("同物料同类型重复建被拒 400", dup.get("_err") == 400, str(dup.get("_detail", "")))
# 空项
empty = call("POST", "/api/qc-standards", {"object_type": "material", "object_id": 99,
                                           "check_type": "ipqc", "items": []}, token=AT)
check("空检验项被拒 400", empty.get("_err") == 400)
# 正常新建（新物料不存在标准 → 用 AUX 类已停用? 直接找一个没标准的: RAW 有7套全配了，用一个新物料）
nu = call("POST", "/api/material", {"code": "TMP-S2", "name": "标准测试料", "material_type": "原料",
                                    "spec": "x", "unit": "t"}, token=AT)
mid = nu.get("id")
cr = call("POST", "/api/qc-standards", {"object_type": "material", "object_id": mid,
                                        "check_type": "iqc", "sample_qty": 1,
                                        "items": [
                                            {"indicator": "主含量", "unit": "%", "min_val": 90, "max_val": 100, "is_key": 1},
                                            {"indicator": "水分", "unit": "%", "min_val": None, "max_val": 0.5},
                                        ]}, token=AT)
check("新建标准成功", cr.get("ok") is True and cr.get("std_no"), str(cr))
sid = cr.get("id")
det = call("GET", "/api/qc-standards/" + str(sid), token=AT)
check("详情含 2 项且限值正确", len(det["items"]) == 2 and det["items"][0]["min_val"] == 90
      and det["items"][1]["max_val"] == 0.5, json.dumps(det["items"], ensure_ascii=False)[:120])
check("版本 v1", det["version"] == 1)

print("══ 3. 维护项目 + 修改留痕 ══")
# 改水分上限 0.5→0.3，加一项"外观"
upd = call("PUT", f"/api/qc-standards/{sid}", {"items": [
    {"indicator": "主含量", "unit": "%", "min_val": 90, "max_val": 100, "is_key": 1},
    {"indicator": "水分", "unit": "%", "min_val": None, "max_val": 0.3},
    {"indicator": "外观", "unit": "", "min_val": None, "max_val": None},
]}, token=QT)
check("维护保存成功(qm 可管理)", upd.get("ok") is True)
det2 = call("GET", f"/api/qc-standards/{sid}", token=AT)
check("保存后 3 项 + 版本 v2", len(det2["items"]) == 3 and det2["version"] == 2)
logs = call("GET", f"/api/qc-standards/{sid}/logs", token=AT)
check("留痕≥2条(建+改)", len(logs) >= 2, f"{len(logs)} 条")
check("留痕含修改人", logs[0]["username"] in ("qm", "admin"))
print("  留痕样例:", logs[0]["username"], logs[0]["detail"])

print("══ 4. 复制模板 ══")
# 把"主含量/水分"标准复制给 TMP 料之外的另一新物料
nu2 = call("POST", "/api/material", {"code": "TMP-S3", "name": "复制目标料", "material_type": "辅料",
                                     "spec": "x", "unit": "t"}, token=AT)
mid2 = nu2.get("id")
cp = call("POST", f"/api/qc-standards/{sid}/copy", {"object_type": "material", "object_id": mid2}, token=AT)
check("复制成功生成新标准", cp.get("ok") is True and cp.get("std_no"), str(cp))
cpdet = call("GET", "/api/qc-standards/" + str(cp.get("id")), token=AT)
check("复制带全 3 项", len(cpdet["items"]) == 3)
check("复制名含新物料", "复制目标料" in cpdet["name"], cpdet["name"])
check("复制备注标注来源", "复制自" in (cpdet.get("remark") or ""), cpdet.get("remark", ""))
# 同对象再复制被拒
cp2 = call("POST", f"/api/qc-standards/{sid}/copy", {"object_type": "material", "object_id": mid2}, token=AT)
check("重复复制被拒 400", cp2.get("_err") == 400)
# 复制后可独立改（互不影响）
cpupd = call("PUT", "/api/qc-standards/" + str(cp.get("id")), {"items": [
    {"indicator": "主含量", "unit": "%", "min_val": 90, "max_val": 100, "is_key": 1},
    {"indicator": "水分", "unit": "%", "min_val": None, "max_val": 0.3},
    {"indicator": "外观", "unit": "", "min_val": None, "max_val": None},
    {"indicator": "粒度", "unit": "mm", "min_val": 1, "max_val": 4},
]}, token=AT)
check("复制品可独立加项", cpupd.get("ok") is True)
src_after = call("GET", f"/api/qc-standards/{sid}", token=AT)
check("源标准不受复制品修改影响(仍3项)", len(src_after["items"]) == 3)

print("══ 5. by-object 接口（供第3步检验录入用）══")
b1 = call("GET", "/api/qc-standards/by-object?object_type=material&object_id=1&check_type=iqc", token=AT)
check("原料#1 来料标准可查", b1 and "七水硫酸亚铁" in b1["name"])
b2 = call("GET", "/api/qc-standards/by-object?object_type=station&object_id=3&check_type=ipqc", token=AT)
check("工序#3合成反应 过程标准可查", b2 and b2["item_count"] >= 3)
b3 = call("GET", "/api/qc-standards/by-object?object_type=material&object_id=99999&check_type=iqc", token=AT)
check("无标准对象返回 null", b3 is None)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第2步验收全部通过")
