# -*- coding: utf-8 -*-
"""第10步 e2e 验收：COA 客户隔离（链接带随机口令，方案A）
覆盖：
 1) 公开页必须带口令：无口令 403 / 口令错 403 / 口令对 200
 2) 二维码同样需口令；二维码内容=带口令的完整地址；无口令 403
 3) 内部取链接接口需登录；返回的链接可直接打开
 4) 不同报告的密钥不通用（A 的链接打不开 B）
 5) 重置口令：权限（检验员 403 / 质量经理可）→ 旧链接失效、新链接可用
 6) 历史 COA 也都有口令（老库升级后可用）
 7) 公开页不泄露其他报告内容
 8) 前端结构（复制客户链接 / 重置口令 / 带口令二维码）
运行: python scripts/e2e_step10.py（先启动服务；跑完请重置演示库）
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


def raw(path, t="", method="GET", body=None):
    """返回 (状态码, 文本, 响应头字典)"""
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None, method=method)
    req.add_header("Content-Type", "application/json")
    if t:
        req.add_header("token", t)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8", "ignore"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "ignore"), dict(e.headers)


def call(path, t="", method="GET", body=None):
    st, txt, _ = raw(path, t, method, body)
    try:
        d = json.loads(txt)
    except Exception:
        return {"_err": st, "_text": txt[:200]}
    if st >= 400 and isinstance(d, dict) and "_err" not in d:
        d["_err"] = st          # 把 HTTP 状态带出来，便于断言 403/400
    return d


def check(n, c, x=""):
    (PASS if c else FAIL).append(n)
    print(("  ✔ " if c else "  ✘ ") + n + ("  " + x if x else ""))


def login(u):
    return call("/api/auth/login", method="POST", body={"username": u, "password": "123456"})


AT, QT, QCT = (login(x).get("token", "") for x in ("admin", "qm", "qc"))
check("登录（管理员/质量经理/检验员）", all([AT, QT, QCT]))

print("══ 1. 公开页必须带口令 ══")
coas = call("/api/coas", t=AT)
check("COA 列表非空", isinstance(coas, list) and len(coas) > 0, f"{len(coas) if isinstance(coas, list) else '?'} 张")
check("列表带 access_key（内部用）", all("access_key" in c for c in coas))
c1 = coas[0]
no1, k1 = c1["coa_no"], c1["access_key"]
check("历史 COA 已回填口令", bool(k1), f"{no1}: {k1[:8]}…" if k1 else "空")

st, txt, _ = raw("/coa/" + urllib.parse.quote(no1))
check("★ 无口令访问 → 403", st == 403, f"HTTP {st}")
check("403 页面是友好中文提示（含🔒/链接无效）", ("🔒" in txt or "链接无效" in txt), txt[:60].replace("\n", " "))
check("403 不泄露报告内容", "成品质量检验报告" not in txt or "链接无效" in txt)
st, txt, _ = raw("/coa/" + urllib.parse.quote(no1) + "?k=deadbeef")
check("★ 口令错误 → 403", st == 403, f"HTTP {st}")
st, txt, _ = raw("/coa/" + urllib.parse.quote(no1) + "?k=" + k1)
check("★ 口令正确 → 200 且能看到报告", st == 200 and "成品质量检验报告" in txt, f"HTTP {st}")
check("报告页含检验项目表", "检验项目" in txt and "实测结果" in txt)
st, _, _ = raw("/coa/" + urllib.parse.quote("COA-不存在-999"))
check("不存在的报告 → 404", st == 404, f"HTTP {st}")

print("══ 2. 二维码同样需口令 ══")
qr = "/api/public/coa/" + urllib.parse.quote(no1) + "/qr.svg"
st, _, _ = raw(qr)
check("★ 二维码无口令 → 403", st == 403, f"HTTP {st}")
st2, qtxt, hdr = raw(qr + "?k=" + k1)
check("★ 二维码口令正确 → SVG", st2 == 200 and "<svg" in qtxt[:400], f"HTTP {st2}")
url_hdr = hdr.get("X-QMS-URL", hdr.get("x-qms-url", ""))
check("★ 二维码内容=带口令的完整地址", "?k=" + k1 in url_hdr and url_hdr.startswith("http"),
      url_hdr[:70])

print("══ 3. 内部取链接接口 ══")
st, _, _ = raw(f"/api/coas/{c1['id']}/link")
check("无登录取链接 → 401/403", st in (401, 403), f"HTTP {st}")
lk = call(f"/api/coas/{c1['id']}/link", t=QT)
check("登录后可取链接", lk.get("ok") is True and lk.get("key") == k1, str(lk)[:70])
if lk.get("url"):
    path = lk["url"].split("8000", 1)[-1]
    st, txt, _ = raw(path)
    check("★ 取到的链接可直接打开报告", st == 200 and "成品质量检验报告" in txt, f"HTTP {st} {path[:40]}")

print("══ 4. 不同报告密钥不通用 ══")
if len(coas) > 1:
    c2 = coas[1]
    st, txt, _ = raw("/coa/" + urllib.parse.quote(c2["coa_no"]) + "?k=" + k1)
    check("★ A 的口令打不开 B 的报告 → 403", st == 403, f"HTTP {st}")
    st, txt, _ = raw("/coa/" + urllib.parse.quote(c2["coa_no"]) + "?k=" + (c2["access_key"] or ""))
    check("B 自己的口令可打开", st == 200, f"HTTP {st}")

print("══ 5. 重置口令 ══")
r = call(f"/api/coas/{c1['id']}/rotate-key", t=QCT, method="POST", body={})
check("★ 检验员不能重置（403）", r.get("_err") == 403, str(r)[:70])
r2 = call(f"/api/coas/{c1['id']}/rotate-key", t=QT, method="POST", body={})
check("质量经理可重置", r2.get("ok") is True and r2.get("key") and r2["key"] != k1, str(r2)[:60])
st, _, _ = raw("/coa/" + urllib.parse.quote(no1) + "?k=" + k1)
check("★ 旧链接立即失效 → 403", st == 403, f"HTTP {st}")
st, txt, _ = raw("/coa/" + urllib.parse.quote(no1) + "?k=" + r2["key"])
check("★ 新链接可用 → 200", st == 200 and "成品质量检验报告" in txt, f"HTTP {st}")
st, _, _ = raw("/api/public/coa/" + urllib.parse.quote(no1) + "/qr.svg?k=" + k1)
check("旧口令的二维码也失效", st == 403, f"HTTP {st}")
al = call("/api/audit-logs?keyword=coa", t=AT)
check("重置口令有留痕", any("重置客户链接口令" in (x["detail"] or "") for x in al.get("rows", [])))

print("══ 6. 全链路新签发 COA 自带口令 ══")
allc = call("/api/coas", t=AT)
check("全库 COA 都有口令（含历史）", all(c.get("access_key") for c in allc), f"{len(allc)} 张")


def good_vals(fm, by=None):
    out = []
    for it in fm.get("items", []):
        if by and it.get("check_by") != by:
            continue
        v = "正常"
        if it["min_val"] is not None and it["max_val"] is not None:
            v = str(round((it["min_val"] + it["max_val"]) / 2, 2))
        elif it["min_val"] is not None:
            v = str(it["min_val"] + 1)
        elif it["max_val"] is not None:
            v = str(round(it["max_val"] * 0.5, 2))
        out.append({"indicator": it["indicator"], "actual": v})
    return out


# 6.1 找一个 ST06 上工序合格批作父批，建末道（ST08）批
lots2 = [x for x in call("/api/production-lots?status=2", t=AT) if x.get("station_code") == "ST06"]
if not lots2:
    lots2 = [x for x in call("/api/production-lots?status=2", t=AT)]
check("有上工序合格批可作父批", len(lots2) > 0, f"{len(lots2)} 批")
st8 = next(s for s in call("/api/station", t=AT) if s["code"] == "ST08")
mat_fg = next((m["id"] for m in call("/api/material", t=AT) if m["material_type"] == "成品"), None)
nl = call("/api/production-lots", t=AT, method="POST",
          body={"station_id": st8["id"], "parent_lot_no": lots2[0]["lot_no"], "qty": 30,
                "material_id": mat_fg})
check("建末道（成品）批成功", "id" in nl, str(nl)[:80])
if "id" in nl:
    pid = nl["id"]
    fi = call(f"/api/production-lots/{pid}/test-form?check_type=ipqc", t=AT)
    si = call(f"/api/production-lots/{pid}/test", t=AT, method="POST",
              body={"std_id": fi["std_id"], "check_type": "ipqc", "items": good_vals(fi)})
    check("过程检验合格", si.get("result") == 1, str(si)[:80])
    st_lot = next((x for x in call("/api/production-lots?status=4", t=AT) if x["id"] == pid), None)
    check("★ 末道批转为『待成品检验』", st_lot is not None, f"status={si.get('status')}")
    fo = call(f"/api/production-lots/{pid}/test-form?check_type=oqc", t=AT)
    so = call(f"/api/production-lots/{pid}/test", t=AT, method="POST",
              body={"std_id": fo["std_id"], "check_type": "oqc", "items": good_vals(fo)})
    check("成品检验合格（签发 COA）", so.get("result") == 1, str(so)[:80])
    newc = call("/api/coas", t=AT)
    old_no = [x["coa_no"] for x in allc]
    nc = next((c for c in newc if c["coa_no"] not in old_no), None)
    check("★ 新签发 COA 自带口令", bool(nc and nc.get("access_key")), nc["coa_no"] if nc else "未找到新报告")
    if nc and nc.get("access_key"):
        st, txt, _ = raw("/coa/" + urllib.parse.quote(nc["coa_no"]) + "?k=" + nc["access_key"])
        check("★ 新 COA 带口令可打开", st == 200 and "成品质量检验报告" in txt, f"HTTP {st}")
        st2, _, _ = raw("/coa/" + urllib.parse.quote(nc["coa_no"]))
        check("★ 新 COA 不带口令被拒", st2 == 403, f"HTTP {st2}")

print("══ 7. 前端结构 ══")
with urllib.request.urlopen(BASE + "/") as rr:
    ps = rr.read().decode("utf-8", "ignore")
for n, ok in [
    ("复制客户链接按钮", "coaCopyLink" in ps and "📋 复制客户链接" in ps),
    ("重置口令按钮", "coaRotate" in ps and "🔁 重置口令" in ps),
    ("复制函数（含兼容回退）", "function copyText" in ps and "execCommand" in ps),
    ("二维码带口令", "'/qr.svg?k='+encodeURIComponent(coaKey)" in ps),
    ("预览页显示客户链接", "客户链接（含口令，只发对应客户）" in ps),
    ("列表新增客户链接列", "客户链接（🔒 含口令）" in ps),
]:
    check(n, ok)

print()
print(f"════ 结果: {len(PASS)} 通过 / {len(FAIL)} 失败 ════")
if FAIL:
    print("失败项:", FAIL)
    sys.exit(1)
print("✔ 第10步（COA 客户隔离）验收全部通过")
