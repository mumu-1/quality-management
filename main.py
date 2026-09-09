# -*- coding: utf-8 -*-
"""生产质量管理系统 · 第1步：地基
登录/令牌 + 角色权限 + 基础资料 CRUD（物料/供应商/客户/车间/工序/班组/设备）
+ Excel 导入/模板下载 + 账号管理 + 审计日志
启动: python main.py  → http://localhost:8000
"""
import csv, hashlib, io, json, os, secrets, sys, uuid, urllib.parse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal, get_db, ensure_schema
import models as M

ensure_schema()

app = FastAPI(title="生产质量管理系统")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

TOKEN_TTL_HOURS = 12

# ═══════════════════════ 角色权限矩阵 ═══════════════════════
# 页面 key → (菜单显示名, 分组, 图标)
PAGES = {
    "dashboard":   ("📊 质量总览", "总览", ""),
    "qcstandard":  ("📋 检验标准库", "质量管控", "qcstandard"),
    "incoming":    ("🚚 来料检验", "质量管控", "incoming"),
    "ncr":         ("⚠️ 不合格处理", "质量管控", "ncr"),
    "material":    ("📦 物料管理", "基础资料", "material"),
    "supplier":    ("🚚 供应商", "基础资料", "supplier"),
    "customer":    ("🤝 客户", "基础资料", "customer"),
    "workshop":    ("🏭 车间管理", "基础资料", "workshop"),
    "station":     ("⚙️ 工序管理", "基础资料", "station"),
    "team":        ("👥 班组管理", "基础资料", "team"),
    "equipment":   ("🔧 设备管理", "基础资料", "equipment"),
    "user":        ("🛠️ 账号管理", "系统管理", "user"),
}
# 角色 → 可访问页面
ROLE_PAGES = {
    "admin":     list(PAGES.keys()),
    "boss":      ["dashboard", "qcstandard", "incoming", "ncr", "material", "supplier", "customer", "workshop", "station", "team", "equipment"],
    "qm":        ["dashboard", "qcstandard", "incoming", "ncr", "material", "supplier", "customer", "workshop", "station", "team", "equipment"],
    "qc":        ["dashboard", "qcstandard", "incoming", "ncr", "material", "equipment"],
    "sampler":   ["dashboard", "incoming", "material"],
    "prodlead":  ["dashboard", "qcstandard", "workshop", "station", "equipment", "team"],
    "buyer":     ["dashboard", "incoming", "ncr", "material", "supplier", "customer"],
    "store":     ["dashboard", "incoming", "ncr", "material", "customer", "workshop"],
}
# 角色 → 可管理(增删改)的页面 key；不在列表 = 只读/仅查看
ROLE_MANAGE = {
    "admin":   ["material", "supplier", "customer", "workshop", "station", "team", "equipment", "user", "qcstandard", "incoming", "ncr"],
    "qm":      ["material", "supplier", "customer", "workshop", "station", "team", "equipment", "qcstandard", "incoming", "ncr"],
    "buyer":   ["supplier", "incoming", "ncr"],
}

# 实体表映射（用于通用 CRUD）
ENTITY_MODEL = {
    "material":  M.Material,
    "supplier":  M.Supplier,
    "customer":  M.Customer,
    "workshop":  M.Workshop,
    "station":   M.Station,
    "team":      M.Team,
    "equipment": M.Equipment,
}
ENTITY_COLS = {
    "material":  ["code", "name", "material_type", "spec", "unit", "remark"],
    "supplier":  ["code", "name", "contact", "phone", "remark"],
    "customer":  ["code", "name", "contact", "phone", "remark"],
    "workshop":  ["code", "name", "remark"],
    "station":   ["code", "name", "workshop_id", "seq", "remark"],
    "team":      ["name", "leader", "remark"],
    "equipment": ["code", "name", "station_id", "category", "remark"],
}


# ═══════════════════════ 认证 ═══════════════════════
def hash_pwd(pwd, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 100_000).hex()
    return salt, h


def check_pwd(pwd, salt, h):
    return hashlib.pbkdf2_hmac("sha256", pwd.encode(), salt.encode(), 100_000).hex() == h


def get_user_by_token(db: Session, token: str):
    row = db.query(M.AuthToken).filter(M.AuthToken.token == token).first()
    if not row or row.expires_at < datetime.now():
        return None
    return db.query(M.User).filter(M.User.id == row.user_id, M.User.enabled == True).first()


def audit(db, username, action, target, detail=""):
    db.add(M.AuditLog(username=username, action=action, target=target, detail=detail))


def _load_json_list(v):
    """解析 JSON 数组列；空/无效 → None（表示按角色默认）"""
    if not v:
        return None
    try:
        arr = json.loads(v)
        return arr if isinstance(arr, list) else None
    except Exception:
        return None


def user_pages(user) -> list:
    """有效页面权限：账号自定义 view_pages > 角色默认模板"""
    custom = _load_json_list(getattr(user, "view_pages", None))
    if custom is not None:
        return [k for k in custom if k in PAGES]
    return ROLE_PAGES.get(user.role_key, ["dashboard"])


def user_manage(user) -> list:
    """有效管理权限：账号自定义 manage_modules > 角色默认模板"""
    custom = _load_json_list(getattr(user, "manage_modules", None))
    if custom is not None:
        return [k for k in custom if k in PAGES]
    return ROLE_MANAGE.get(user.role_key, [])


def page_meta(user) -> dict:
    """登录返回的页面/权限信息（自定义优先，角色模板兜底）"""
    pages = user_pages(user)
    manage = user_manage(user)
    menus = []
    for key in pages:
        name, group, ico = PAGES[key]
        menus.append({"key": key, "name": name, "group": group, "icon": ico,
                      "manage": key in manage})
    return {"menus": menus, "manage": manage}


def get_user_stations(db, user_id) -> list:
    """账号负责的全部工序 id（多对多表）"""
    return [r.station_id for r in db.query(M.UserStation)
            .filter(M.UserStation.user_id == user_id).all()]


class LoginIn(BaseModel):
    username: str
    password: str


@app.post("/api/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.query(M.User).filter(M.User.username == body.username.strip()).first()
    if not u or not check_pwd(body.password, u.password_salt, u.password_hash):
        raise HTTPException(401, "用户名或密码错误")
    if not u.enabled:
        raise HTTPException(403, "账号已停用，请联系管理员")
    token = uuid.uuid4().hex
    db.add(M.AuthToken(token=token, user_id=u.id,
                       expires_at=datetime.now() + timedelta(hours=TOKEN_TTL_HOURS)))
    db.commit()
    pm = page_meta(u)
    return {"token": token, "user": {
        "id": u.id, "username": u.username, "real_name": u.real_name,
        "department": u.department, "role": u.role_key, "station_id": u.station_id,
        "station_ids": get_user_stations(db, u.id) or ([u.station_id] if u.station_id else []),
        "perm_custom": _load_json_list(u.view_pages) is not None or _load_json_list(u.manage_modules) is not None,
    }, "menus": pm["menus"]}


@app.post("/api/auth/logout")
def logout(token: str = Header(""), db: Session = Depends(get_db)):
    if token:
        db.query(M.AuthToken).filter(M.AuthToken.token == token).delete()
        db.commit()
    return {"ok": True}


def require_user(token: str, db: Session = Depends(get_db)):
    if not token:
        raise HTTPException(401, "未登录")
    u = get_user_by_token(db, token)
    if not u:
        raise HTTPException(401, "登录已过期，请重新登录")
    return u


def require_page(user, page_key):
    if page_key not in user_pages(user):
        raise HTTPException(403, "无权限访问该模块")


def require_manage(user, page_key):
    if page_key not in user_manage(user):
        raise HTTPException(403, "该模块为只读，无修改权限")


# ═══════════════════════ 通用 CRUD ═══════════════════════
def _row_meta(m):
    d = {"id": m.id}
    for c in ENTITY_COLS.get(m.__tablename__, []):
        d[c] = getattr(m, c)
    return d


def _clean(data: dict, cols):
    out = {}
    for c in cols:
        v = data.get(c)
        if isinstance(v, str):
            v = v.strip()
        if v == "" or v is None:
            v = None if c not in ("code", "name") else ""
        out[c] = v
    return out


def _find_conflict(db, model, field, value, exclude_id=None):
    if not value:
        return None
    q = db.query(model).filter(getattr(model, field) == value)
    if exclude_id:
        q = q.filter(model.id != exclude_id)
    return q.first()


def list_entity(entity, keyword, db, user):
    require_page(user, entity)
    model = ENTITY_MODEL[entity]
    q = db.query(model).filter(model.enabled == True)
    if keyword:
        like = f"%{keyword}%"
        if entity == "station":
            q = q.join(M.Workshop).filter(or_(model.code.like(like), model.name.like(like), M.Workshop.name.like(like)))
        elif entity == "equipment":
            q = q.join(M.Station).filter(or_(model.code.like(like), model.name.like(like), M.Station.name.like(like)))
        else:
            q = q.filter(or_(model.code.like(like), model.name.like(like)))
    rows = q.order_by(model.id).all()
    out = []
    for m in rows:
        d = _row_meta(m)
        if entity == "station" and m.workshop:
            d["workshop_name"] = m.workshop.name
            d["next_name"] = None
        if entity == "equipment" and m.station:
            d["station_name"] = m.station.name
            d["workshop_name"] = m.station.workshop.name if m.station.workshop else None
        out.append(d)
    return out


def create_entity(entity, data, db, user):
    require_manage(user, entity)
    model = ENTITY_MODEL[entity]
    cols = ENTITY_COLS[entity]
    body = _clean(data, cols)
    if "code" in cols and body.get("code"):
        if _find_conflict(db, model, "code", body["code"]):
            raise HTTPException(400, f"编码 {body['code']} 已存在")
    if entity == "station":
        body["workshop_id"] = data.get("workshop_id") or 1
        if data.get("seq") is not None:
            try: body["seq"] = int(data["seq"])
            except: pass
    if entity == "equipment":
        if not body.get("station_id"):
            raise HTTPException(400, "必须选择所属工序")
    m = model(**body)
    db.add(m); db.commit(); db.refresh(m)
    audit(db, user.username, "create", f"{entity}:{body.get('code') or body.get('name')}", "")
    db.commit()
    return {"ok": True, "id": m.id}


def update_entity(entity, eid, data, db, user):
    require_manage(user, entity)
    model = ENTITY_MODEL[entity]
    m = db.get(model, eid)
    if not m:
        raise HTTPException(404, "记录不存在")
    cols = ENTITY_COLS[entity]
    # 部分更新：只更新请求中出现的字段，其余保留原值（避免编辑弹窗清空未展示字段）
    present = {c: data.get(c) for c in cols if c in data}
    body = _clean(present, list(present.keys()))
    if "code" in body and body.get("code"):
        c = _find_conflict(db, model, "code", body["code"], exclude_id=eid)
        if c:
            raise HTTPException(400, f"编码 {body['code']} 已存在")
    for c, v in body.items():
        setattr(m, c, v)
    db.commit()
    audit(db, user.username, "update", f"{entity}:{eid}", "")
    db.commit()
    return {"ok": True}


def delete_entity(entity, eid, db, user):
    require_manage(user, entity)
    model = ENTITY_MODEL[entity]
    m = db.get(model, eid)
    if not m:
        raise HTTPException(404, "记录不存在")
    # 引用保护：工序被设备/人员引用、车间被工序引用时禁止删除
    if entity == "station":
        if db.query(M.Equipment).filter(M.Equipment.station_id == eid).first():
            raise HTTPException(400, "该工序下还有设备，不能删除（可先停用）")
        if db.query(M.User).filter(M.User.station_id == eid).first():
            raise HTTPException(400, "该工序还绑定着人员账号，不能删除")
    if entity == "workshop":
        if db.query(M.Station).filter(M.Station.workshop_id == eid).first():
            raise HTTPException(400, "该车间下还有工序，不能删除")
    if entity == "material":
        # 物料被使用保护（后续批次表建好后在此扩展）
        pass
    m.enabled = False   # 软删除
    db.commit()
    audit(db, user.username, "delete", f"{entity}:{eid}", "")
    db.commit()
    return {"ok": True}


def entity_routes(entity):
    @app.get(f"/api/{entity}")
    def _list(keyword: str = "", token: str = Header(""), db: Session = Depends(get_db)):
        u = require_user(token, db)
        return list_entity(entity, keyword, db, u)

    @app.post(f"/api/{entity}")
    def _create(data: dict, token: str = Header(""), db: Session = Depends(get_db)):
        u = require_user(token, db)
        return create_entity(entity, data, db, u)

    @app.put(f"/api/{entity}/{{eid}}")
    def _update(eid: int, data: dict, token: str = Header(""), db: Session = Depends(get_db)):
        u = require_user(token, db)
        return update_entity(entity, eid, data, db, u)

    @app.delete(f"/api/{entity}/{{eid}}")
    def _delete(eid: int, token: str = Header(""), db: Session = Depends(get_db)):
        u = require_user(token, db)
        return delete_entity(entity, eid, db, u)


for e in ENTITY_MODEL:
    entity_routes(e)


# ═══════════════════════ 检验标准库（第2步） ═══════════════════════
CHECK_TYPE_NAMES = {"iqc": "来料检验IQC", "ipqc": "过程检验IPQC", "oqc": "成品检验OQC"}
OBJ_TYPE_NAMES = {"material": "物料", "station": "工序"}


def _std_object_name(db, otype, oid):
    if otype == "material":
        m = db.get(M.Material, oid)
        return f"{m.name}（{m.code}）" if m else f"物料#{oid}"
    if otype == "station":
        s = db.get(M.Station, oid)
        return f"{s.name}（{s.code}）" if s else f"工序#{oid}"
    return str(oid)


def _std_next_no(db):
    row = db.query(M.QcStandard).order_by(M.QcStandard.id.desc()).first()
    n = (int(row.std_no.split("-")[1]) + 1) if row else 1
    return f"STD-{n:03d}"


def std_out(db, s, with_items=False):
    d = {"id": s.id, "std_no": s.std_no, "name": s.name,
         "object_type": s.object_type, "object_id": s.object_id,
         "object_name": _std_object_name(db, s.object_type, s.object_id),
         "check_type": s.check_type, "check_type_name": CHECK_TYPE_NAMES.get(s.check_type, s.check_type),
         "sample_qty": s.sample_qty, "remark": s.remark, "status": s.status,
         "version": s.version, "updated_by": s.updated_by,
         "item_count": db.query(M.QcStandardItem).filter(
             M.QcStandardItem.standard_id == s.id, M.QcStandardItem.enabled == 1).count(),
         "updated_at": s.updated_at.strftime("%Y-%m-%d %H:%M") if s.updated_at else ""}
    if with_items:
        items = db.query(M.QcStandardItem).filter(
            M.QcStandardItem.standard_id == s.id, M.QcStandardItem.enabled == 1
        ).order_by(M.QcStandardItem.seq).all()
        d["items"] = [{"id": it.id, "seq": it.seq, "indicator": it.indicator, "unit": it.unit,
                       "min_val": it.min_val, "max_val": it.max_val, "method": it.method,
                       "is_key": it.is_key} for it in items]
    return d


@app.get("/api/qc-standards")
def std_list(check_type: str = "", keyword: str = "", token: str = Header(""),
             db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "qcstandard")
    q = db.query(M.QcStandard)
    if check_type:
        q = q.filter(M.QcStandard.check_type == check_type)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(M.QcStandard.name.like(like))
    rows = q.order_by(M.QcStandard.check_type, M.QcStandard.id).all()
    return [std_out(db, s) for s in rows]


@app.get("/api/qc-standards/by-object")
def std_by_object(object_type: str, object_id: int, check_type: str = "", token: str = Header(""),
                  db: Session = Depends(get_db)):
    """供检验录入用：取某物料/工序已启用的标准（注意：须在 /{sid} 之前声明，否则被路径参数吞掉）"""
    u = require_user(token, db)
    require_page(u, "qcstandard")
    q = db.query(M.QcStandard).filter(
        M.QcStandard.object_type == object_type, M.QcStandard.object_id == object_id,
        M.QcStandard.status == 1)
    if check_type:
        q = q.filter(M.QcStandard.check_type == check_type)
    s = q.order_by(M.QcStandard.id.desc()).first()
    if not s:
        return None
    return std_out(db, s, with_items=True)


@app.get("/api/qc-standards/{sid}")
def std_detail(sid: int, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "qcstandard")
    s = db.get(M.QcStandard, sid)
    if not s:
        raise HTTPException(404, "标准不存在")
    return std_out(db, s, with_items=True)


def _check_dup(db, otype, oid, ctype, exclude_id=None):
    """同一 对象×类型 只能有一套启用标准"""
    q = db.query(M.QcStandard).filter(
        M.QcStandard.object_type == otype, M.QcStandard.object_id == oid,
        M.QcStandard.check_type == ctype, M.QcStandard.status == 1)
    if exclude_id:
        q = q.filter(M.QcStandard.id != exclude_id)
    return q.first()


class StdIn(BaseModel):
    object_type: str
    object_id: int
    check_type: str          # iqc/ipqc/oqc
    name: str = ""
    sample_qty: int = 1
    remark: str = ""
    items: list = []         # [{indicator,unit,min_val,max_val,method,is_key}]


@app.post("/api/qc-standards")
def std_create(body: StdIn, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_manage(u, "qcstandard")
    if body.object_type not in ("material", "station"):
        raise HTTPException(400, "object_type 须为 material/station")
    if body.check_type not in CHECK_TYPE_NAMES:
        raise HTTPException(400, "check_type 须为 iqc/ipqc/oqc")
    if _check_dup(db, body.object_type, body.object_id, body.check_type):
        raise HTTPException(400, "该对象已存在启用中的同类型检验标准（可编辑旧标准或先停用）")
    if not body.items:
        raise HTTPException(400, "请至少配置一个检验项目")
    name = body.name or f"{_std_object_name(db, body.object_type, body.object_id)}-{CHECK_TYPE_NAMES.get(body.check_type)}标准"
    s = M.QcStandard(std_no=_std_next_no(db), name=name, object_type=body.object_type,
                     object_id=body.object_id, check_type=body.check_type,
                     sample_qty=body.sample_qty or 1, remark=body.remark,
                     status=1, version=1, updated_by=u.username)
    db.add(s); db.flush()
    _replace_items(db, s.id, body.items, u.username)
    db.commit()
    audit(db, u.username, "create", f"qcstandard:{s.std_no}", f"新建标准 {name}（{len(body.items)}项）")
    db.commit()
    return {"ok": True, "id": s.id, "std_no": s.std_no}


def _replace_items(db, sid, items, username):
    """整单替换明细（编辑保存=删旧插新），并返回 diff 描述"""
    old = {it.indicator: it for it in db.query(M.QcStandardItem).filter(
        M.QcStandardItem.standard_id == sid, M.QcStandardItem.enabled == 1).all()}
    new_names = []
    changed = []
    for i, it in enumerate(items, start=1):
        indicator = (it.get("indicator") or "").strip()
        if not indicator:
            continue
        new_names.append(indicator)
        try:
            lo = float(it["min_val"]) if it.get("min_val") not in (None, "") else None
            hi = float(it["max_val"]) if it.get("max_val") not in (None, "") else None
        except (ValueError, TypeError):
            raise HTTPException(400, f"指标「{indicator}」的限值不是数字")
        o = old.get(indicator)
        if o and (o.min_val != lo or o.max_val != hi or o.unit != it.get("unit", "") or o.method != it.get("method", "")):
            changed.append(f"{indicator}")
        if o:
            o.seq = i; o.unit = it.get("unit", ""); o.min_val = lo; o.max_val = hi
            o.method = it.get("method", ""); o.is_key = 1 if it.get("is_key") else 0
        else:
            db.add(M.QcStandardItem(standard_id=sid, seq=i, indicator=indicator,
                                    unit=it.get("unit", ""), min_val=lo, max_val=hi,
                                    method=it.get("method", ""),
                                    is_key=1 if it.get("is_key") else 0))
    removed = [name for name in old if name not in new_names]
    for name in removed:
        old[name].enabled = 0
    # 记录变更
    parts = []
    if removed: parts.append(f"删除 {len(removed)} 项：{'、'.join(removed[:5])}")
    if changed: parts.append(f"修改 {len(changed)} 项：{'、'.join(changed[:5])}")
    added_n = len(new_names) - len([x for x in old.values() if x.enabled and x.indicator in new_names])
    if added_n > 0: parts.append(f"新增 {added_n} 项")
    # 注意：此处不递增 version——新建由调用方置 1，更新由 std_update 递增
    s = db.get(M.QcStandard, sid)
    if s:
        s.updated_at = datetime.now()
        s.updated_by = username
    return "；".join(parts) or "明细调整"


class StdUpdateIn(BaseModel):
    name: str | None = None
    sample_qty: int | None = None
    remark: str | None = None
    status: int | None = None
    items: list | None = None


@app.put("/api/qc-standards/{sid}")
def std_update(sid: int, body: StdUpdateIn, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_manage(u, "qcstandard")
    s = db.get(M.QcStandard, sid)
    if not s:
        raise HTTPException(404, "标准不存在")
    if body.name is not None: s.name = body.name
    if body.sample_qty is not None: s.sample_qty = body.sample_qty
    if body.remark is not None: s.remark = body.remark
    diff = ""
    if body.items is not None:
        if not body.items:
            raise HTTPException(400, "至少保留一个检验项目")
        diff = _replace_items(db, sid, body.items, u.username)
        s.version = (s.version or 1) + 1   # 仅"更新明细"递增版本
    s.updated_at = datetime.now(); s.updated_by = u.username
    db.commit()
    audit(db, u.username, "update", f"qcstandard:{s.std_no}",
          diff or f"更新标准头（{s.name}）")
    db.commit()
    return {"ok": True}


@app.post("/api/qc-standards/{sid}/copy")
def std_copy(sid: int, body: dict, token: str = Header(""), db: Session = Depends(get_db)):
    """模板复制：把 sid 的标准复制给另一个 物料/工序（同类检验类型）"""
    u = require_user(token, db)
    require_manage(u, "qcstandard")
    src = db.get(M.QcStandard, sid)
    if not src:
        raise HTTPException(404, "源标准不存在")
    otype = body.get("object_type") or src.object_type
    oid = body.get("object_id")
    if not oid:
        raise HTTPException(400, "请选择要复制给的对象")
    if _check_dup(db, otype, int(oid), src.check_type):
        raise HTTPException(400, "目标对象已存在同类型标准，不能重复复制")
    name = body.get("name") or f"{_std_object_name(db, otype, int(oid))}-{CHECK_TYPE_NAMES.get(src.check_type)}标准"
    items = [{"indicator": it.indicator, "unit": it.unit, "min_val": it.min_val,
              "max_val": it.max_val, "method": it.method, "is_key": it.is_key}
             for it in db.query(M.QcStandardItem).filter(
                 M.QcStandardItem.standard_id == sid, M.QcStandardItem.enabled == 1)
             .order_by(M.QcStandardItem.seq).all()]
    s = M.QcStandard(std_no=_std_next_no(db), name=name, object_type=otype,
                     object_id=int(oid), check_type=src.check_type,
                     sample_qty=src.sample_qty, remark=f"复制自 {src.std_no} {src.name}",
                     status=1, version=1, updated_by=u.username)
    db.add(s); db.flush()
    _replace_items(db, s.id, items, u.username)
    db.commit()
    audit(db, u.username, "create", f"qcstandard:{s.std_no}",
          f"复制自 {src.std_no} → {name}（{len(items)}项）")
    db.commit()
    return {"ok": True, "id": s.id, "std_no": s.std_no}


@app.get("/api/qc-standards/{sid}/logs")
def std_logs(sid: int, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "qcstandard")
    s = db.get(M.QcStandard, sid)
    if not s:
        raise HTTPException(404, "标准不存在")
    rows = db.query(M.AuditLog).filter(
        M.AuditLog.target == f"qcstandard:{s.std_no}"
    ).order_by(M.AuditLog.id.desc()).limit(30).all()
    return [{"username": r.username, "action": r.action, "detail": r.detail,
             "time": r.created_at.strftime("%Y-%m-%d %H:%M") if r.created_at else ""} for r in rows]


# ═══════════════════════ 来料检验闭环（第3步）═══════════════════════
LOT_STATUS = {0: "待取样", 1: "待检验", 2: "合格放行", 3: "不合格冻结", 4: "让步接收", 5: "拒收退货", 6: "报废"}
NCR_STATUS = {0: "待处理", 1: "已拒收退货", 2: "已让步接收", 3: "已报废"}


def _next_no(db, model, col, prefix):
    """通用序号：取同前缀最大尾部序号+1。prefix 如 'T-20260909-' 或 'NCR-2026-'"""
    rows = db.query(model).filter(getattr(model, col).like(prefix + "%")).all()
    mx = 0
    for r in rows:
        tail = str(getattr(r, col))[len(prefix):]
        try:
            mx = max(mx, int(tail))
        except ValueError:
            pass
    return f"{prefix}{mx + 1:03d}"


def _lot_out(db, lot):
    m = lot.material or db.get(M.Material, lot.material_id)
    s = lot.supplier or db.get(M.Supplier, lot.supplier_id)
    latest = db.query(M.TestRecord).filter(M.TestRecord.lot_id == lot.id) \
        .order_by(M.TestRecord.id.desc()).first()
    ncr = db.query(M.Ncr).filter(M.Ncr.lot_id == lot.id).order_by(M.Ncr.id.desc()).first()
    return {
        "id": lot.id, "lot_no": lot.lot_no,
        "material_id": lot.material_id,
        "material_name": f"{m.name}（{m.code}）" if m else "",
        "material_type": m.material_type if m else "",
        "supplier_id": lot.supplier_id,
        "supplier_name": s.name if s else "",
        "supplier_lot": lot.supplier_lot, "qty": lot.qty, "unit": lot.unit,
        "vehicle": lot.vehicle, "status": lot.status,
        "status_name": LOT_STATUS.get(lot.status, str(lot.status)),
        "arrival_by": lot.arrival_by,
        "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M") if lot.created_at else "",
        "latest_result": latest.result if latest else None,
        "ncr_no": ncr.ncr_no if ncr else None,
        "ncr_status": NCR_STATUS.get(ncr.status) if ncr else None,
    }


def _require_action(user, allowed_roles):
    if user.role_key not in allowed_roles:
        raise HTTPException(403, "当前角色无此操作权限")


class LotIn(BaseModel):
    material_id: int
    supplier_id: int
    supplier_lot: str = ""
    qty: float = 0
    vehicle: str = ""
    remark: str = ""


@app.get("/api/incoming")
def incoming_list(status: str = "", keyword: str = "", token: str = Header(""),
                 db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "incoming")
    q = db.query(M.IncomingLot)
    if status != "" and status is not None:
        q = q.filter(M.IncomingLot.status == int(status))
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(M.IncomingLot.lot_no.like(like))
    rows = q.order_by(M.IncomingLot.id.desc()).limit(300).all()
    return [_lot_out(db, lot) for lot in rows]


@app.post("/api/incoming")
def incoming_create(body: LotIn, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "incoming")
    _require_action(u, ["admin", "qm", "buyer"])   # 采购/质量可登记到货
    m = db.get(M.Material, body.material_id)
    s = db.get(M.Supplier, body.supplier_id)
    if not m or not s:
        raise HTTPException(400, "物料或供应商不存在")
    today = datetime.now().strftime("%Y%m%d")
    sup_digits = ("".join(ch for ch in s.code if ch.isdigit())[-2:].zfill(2)
                  if s.code else str(s.id).zfill(2))
    lot_no = _next_no(db, M.IncomingLot, "lot_no", f"{today}-RA-SUP{sup_digits}-")
    lot = M.IncomingLot(lot_no=lot_no, material_id=body.material_id,
                        supplier_id=body.supplier_id, supplier_lot=body.supplier_lot.strip(),
                        qty=body.qty, unit=m.unit or "t", vehicle=body.vehicle.strip(),
                        status=0, arrival_by=u.username, remark=body.remark.strip())
    db.add(lot); db.commit()
    audit(db, u.username, "create", f"incoming:{lot_no}", f"到货登记 {m.name} {body.qty}{m.unit}")
    db.commit()
    return {"ok": True, "id": lot.id, "lot_no": lot_no}


class SampleIn(BaseModel):
    sample_qty: str = ""


@app.post("/api/incoming/{lot_id}/sample")
def incoming_sample(lot_id: int, body: SampleIn, token: str = Header(""),
                    db: Session = Depends(get_db)):
    """取样：待取样批次 → 生成取样单，批次转待检验"""
    u = require_user(token, db)
    require_page(u, "incoming")
    _require_action(u, ["admin", "qm", "qc", "sampler"])   # 取样员/检验员可取样
    lot = db.get(M.IncomingLot, lot_id)
    if not lot:
        raise HTTPException(404, "批次不存在")
    if lot.status != 0:
        raise HTTPException(400, f"该批当前状态为{LOT_STATUS.get(lot.status)}，不能取样")
    today = datetime.now().strftime("%Y%m%d")
    sp_no = _next_no(db, M.Sample, "sample_no", f"SP-{today}-")
    db.add(M.Sample(sample_no=sp_no, lot_id=lot.id, sample_by=u.username,
                    sample_qty=body.sample_qty.strip()))
    lot.status = 1
    lot.updated_at = datetime.now()
    db.commit()
    audit(db, u.username, "update", f"incoming:{lot.lot_no}", f"取样 {sp_no}")
    db.commit()
    return {"ok": True, "sample_no": sp_no}


@app.get("/api/incoming/{lot_id}/test-form")
def incoming_test_form(lot_id: int, token: str = Header(""), db: Session = Depends(get_db)):
    """检验模板：按 物料×iqc 匹配启用标准，带出全部检验项（限值只读，判定以后端为准）"""
    u = require_user(token, db)
    require_page(u, "incoming")
    lot = db.get(M.IncomingLot, lot_id)
    if not lot:
        raise HTTPException(404, "批次不存在")
    std = db.query(M.QcStandard).filter(
        M.QcStandard.object_type == "material", M.QcStandard.object_id == lot.material_id,
        M.QcStandard.check_type == "iqc", M.QcStandard.status == 1).first()
    if not std:
        raise HTTPException(400, "该物料还没有启用中的来料检验标准，请先在「检验标准库」配置")
    items = db.query(M.QcStandardItem).filter(
        M.QcStandardItem.standard_id == std.id, M.QcStandardItem.enabled == 1
    ).order_by(M.QcStandardItem.seq).all()
    return {"std_id": std.id, "std_no": std.std_no, "std_name": std.name,
            "sample_qty": std.sample_qty,
            "items": [{"indicator": it.indicator, "unit": it.unit,
                       "min_val": it.min_val, "max_val": it.max_val,
                       "method": it.method, "is_key": it.is_key} for it in items]}


class TestIn(BaseModel):
    std_id: int
    items: list      # [{indicator, actual}]


def _judge_value(actual, lo, hi):
    """判定单个数值项：返回 (pass, note)。文本项无上下限 → 非空即过"""
    actual_s = str(actual).strip()
    if lo is None and hi is None:
        return True, ""
    try:
        v = float(actual_s)
    except ValueError:
        return False, f"实测「{actual_s}」不是数字"
    if lo is not None and v < lo:
        return False, f"实测 {v}{''} 低于标准下限 {lo}"
    if hi is not None and v > hi:
        return False, f"实测 {v} 超出标准上限 {hi}"
    return True, ""


@app.post("/api/incoming/{lot_id}/test")
def incoming_test(lot_id: int, body: TestIn, token: str = Header(""),
                  db: Session = Depends(get_db)):
    """提交检验：后端按标准限值逐项判定 → 全合格放行 / 有不合格自动生成 NCR 并冻结"""
    u = require_user(token, db)
    require_page(u, "incoming")
    _require_action(u, ["admin", "qm", "qc"])       # 检验员/质检提交
    lot = db.get(M.IncomingLot, lot_id)
    if not lot:
        raise HTTPException(404, "批次不存在")
    if lot.status != 1:
        raise HTTPException(400, f"该批当前状态为{LOT_STATUS.get(lot.status)}，只有待检验批次可提交检验")
    std = db.get(M.QcStandard, body.std_id)
    if not std:
        raise HTTPException(400, "标准不存在")
    std_items = {it.indicator: it for it in db.query(M.QcStandardItem).filter(
        M.QcStandardItem.standard_id == std.id, M.QcStandardItem.enabled == 1).all()}
    today = datetime.now().strftime("%Y%m%d")
    test_no = _next_no(db, M.TestRecord, "test_no", f"T-{today}-")
    tr = M.TestRecord(test_no=test_no, lot_id=lot.id, std_id=std.id,
                      check_type="iqc", result=0, tested_by=u.username)
    db.add(tr); db.flush()
    fail_parts, fail_n, judged_n = [], 0, 0
    for seq, it in enumerate(body.items, start=1):
        indicator = (it.get("indicator") or "").strip()
        actual = str(it.get("actual", "")).strip()
        if not indicator:
            continue
        s = std_items.get(indicator)
        if not s:
            continue
        judged_n += 1
        lo, hi = s.min_val, s.max_val
        if s.min_val is None and s.max_val is None:
            passed = actual != ""          # 外观等文本项：填了就算过（人工目测）
            note = ""
        else:
            passed, note = _judge_value(actual, lo, hi)
        db.add(M.TestItem(test_id=tr.id, seq=seq, indicator=indicator, unit=s.unit,
                          min_val=lo, max_val=hi, method=s.method, is_key=s.is_key,
                          actual=actual, pass_flag=1 if passed else 0))
        if not passed:
            fail_n += 1
            lim = []
            if lo is not None: lim.append(f"≥{lo}")
            if hi is not None: lim.append(f"≤{hi}")
            fail_parts.append(f"{indicator} 实测{actual}（标准{'/'.join(lim) or s.unit}）{note}")
    if judged_n == 0:
        db.delete(tr); db.commit()
        raise HTTPException(400, "没有可判定的检验项（请按模板逐项录入实测值）")
    if fail_n:
        tr.result = 2
        lot.status = 3                     # 不合格冻结
        summary = "；".join(fail_parts[:8])
        ncr_no = _next_no(db, M.Ncr, "ncr_no", f"NCR-{datetime.now().year}-")
        db.add(M.Ncr(ncr_no=ncr_no, lot_id=lot.id, test_id=tr.id, fail_summary=summary,
                     status=0, created_by=u.username))
        audit(db, u.username, "create", f"ncr:{ncr_no}", f"检验不合格自动开单：{summary[:80]}")
    else:
        tr.result = 1
        lot.status = 2                     # 合格放行
        audit(db, u.username, "update", f"incoming:{lot.lot_no}", f"检验合格放行 {test_no}")
    lot.updated_at = datetime.now()
    db.commit()
    return {"ok": True, "test_no": test_no, "result": tr.result,
            "fail_summary": "；".join(fail_parts[:8]) if fail_n else "",
            "ncr_no": ncr_no if fail_n else None}


@app.get("/api/tests")
def test_list(check_type: str = "iqc", result: str = "", keyword: str = "",
              token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "incoming")
    q = db.query(M.TestRecord)
    if check_type:
        q = q.filter(M.TestRecord.check_type == check_type)
    if result != "" and result is not None:
        q = q.filter(M.TestRecord.result == int(result))
    if keyword:
        q = q.join(M.IncomingLot).filter(M.IncomingLot.lot_no.like(f"%{keyword}%"))
    rows = q.order_by(M.TestRecord.id.desc()).limit(200).all()
    out = []
    for tr in rows:
        lot = tr.lot or db.get(M.IncomingLot, tr.lot_id)
        m = db.get(M.Material, lot.material_id) if lot else None
        items = db.query(M.TestItem).filter(M.TestItem.test_id == tr.id) \
            .order_by(M.TestItem.seq).all()
        out.append({
            "id": tr.id, "test_no": tr.test_no, "lot_no": lot.lot_no if lot else "",
            "material_name": f"{m.name}（{m.code}）" if m else "",
            "result": tr.result,
            "result_name": {0: "检验中", 1: "合格", 2: "不合格"}.get(tr.result, "?"),
            "tested_by": tr.tested_by,
            "created_at": tr.created_at.strftime("%Y-%m-%d %H:%M") if tr.created_at else "",
            "item_count": len(items),
            "fail_items": [{"indicator": it.indicator, "actual": it.actual,
                            "min_val": it.min_val, "max_val": it.max_val}
                           for it in items if it.pass_flag == 0],
        })
    return out


@app.get("/api/tests/{tid}")
def test_detail(tid: int, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "incoming")
    tr = db.get(M.TestRecord, tid)
    if not tr:
        raise HTTPException(404, "检验单不存在")
    lot = tr.lot or db.get(M.IncomingLot, tr.lot_id)
    m = db.get(M.Material, lot.material_id) if lot else None
    items = db.query(M.TestItem).filter(M.TestItem.test_id == tr.id) \
        .order_by(M.TestItem.seq).all()
    return {"test_no": tr.test_no, "lot_no": lot.lot_no if lot else "",
            "material_name": m.name if m else "", "result": tr.result,
            "tested_by": tr.tested_by,
            "created_at": tr.created_at.strftime("%Y-%m-%d %H:%M") if tr.created_at else "",
            "items": [{"indicator": it.indicator, "unit": it.unit, "actual": it.actual,
                       "min_val": it.min_val, "max_val": it.max_val,
                       "pass": it.pass_flag} for it in items]}


@app.get("/api/ncr")
def ncr_list(status: str = "", keyword: str = "", token: str = Header(""),
             db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "ncr")
    q = db.query(M.Ncr)
    if status != "" and status is not None:
        q = q.filter(M.Ncr.status == int(status))
    if keyword:
        q = q.join(M.IncomingLot).filter(M.IncomingLot.lot_no.like(f"%{keyword}%"))
    rows = q.order_by(M.Ncr.id.desc()).limit(200).all()
    out = []
    for ncr in rows:
        lot = ncr.lot or db.get(M.IncomingLot, ncr.lot_id)
        m = db.get(M.Material, lot.material_id) if lot else None
        out.append({"id": ncr.id, "ncr_no": ncr.ncr_no,
                    "lot_no": lot.lot_no if lot else "",
                    "material_name": m.name if m else "",
                    "supplier_id": lot.supplier_id if lot else None,
                    "fail_summary": ncr.fail_summary,
                    "status": ncr.status,
                    "status_name": NCR_STATUS.get(ncr.status, "?"),
                    "created_by": ncr.created_by,
                    "created_at": ncr.created_at.strftime("%Y-%m-%d %H:%M") if ncr.created_at else "",
                    "disposition": ncr.disposition})
    return out


class NcrDisposeIn(BaseModel):
    disposition: str        # reject 拒收退货 / waive 让步接收 / scrap 报废
    remark: str = ""


@app.post("/api/ncr/{nid}/dispose")
def ncr_dispose(nid: int, body: NcrDisposeIn, token: str = Header(""),
                db: Session = Depends(get_db)):
    """处置不合格：reject(采购/质检) / waive(需质量经理审批) / scrap(需质量经理审批)
    检验员不可处置（自己检的不合格不能自己放行）"""
    u = require_user(token, db)
    require_page(u, "ncr")
    ncr = db.get(M.Ncr, nid)
    if not ncr:
        raise HTTPException(404, "NCR 不存在")
    if ncr.status != 0:
        raise HTTPException(400, "该 NCR 已处理，不能重复处置")
    d = body.disposition
    if d not in ("reject", "waive", "scrap"):
        raise HTTPException(400, "处置类型须为 reject/waive/scrap")
    # 权限矩阵
    if d == "reject":
        _require_action(u, ["admin", "qm", "buyer"])     # 采购主导退货
        lot_status, ncr_status = 5, 1
    elif d == "waive":
        _require_action(u, ["admin", "qm"])              # 让步接收=质量经理审批
        lot_status, ncr_status = 4, 2
    else:
        _require_action(u, ["admin", "qm"])              # 报废=质量经理审批
        lot_status, ncr_status = 6, 3
    ncr.disposition = d
    ncr.status = ncr_status
    ncr.handled_by = u.username
    ncr.handled_at = datetime.now()
    ncr.remark = body.remark.strip()
    lot = db.get(M.IncomingLot, ncr.lot_id)
    if lot:
        lot.status = lot_status
        lot.updated_at = datetime.now()
    db.commit()
    audit(db, u.username, "update", f"ncr:{ncr.ncr_no}",
          f"处置={d} {body.remark.strip()}")
    db.commit()
    return {"ok": True, "lot_status": lot_status, "ncr_status": ncr_status}


# ═══════════════════════ 权限矩阵（供账号页勾选）═══════════════════════
@app.get("/api/admin/perm-matrix")
def perm_matrix(token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "user"); require_manage(u, "user")
    pages = []
    for key, (name, group, ico) in PAGES.items():
        pages.append({"key": key, "name": name, "group": group})
    return {
        "pages": pages,
        "roles": [{"key": k, "name": ROLE_NAMES.get(k, k),
                   "pages": ROLE_PAGES.get(k, []),
                   "manage": ROLE_MANAGE.get(k, [])} for k in ROLE_PAGES],
    }


# ═══════════════════════ 账号管理 ═══════════════════════
ROLE_NAMES = {"admin": "系统管理员", "boss": "高层", "qm": "质量经理", "qc": "检验员",
              "sampler": "取样员", "prodlead": "班组长/主管", "buyer": "采购", "store": "仓储"}


def _save_user_stations(db, user_id, station_ids, keep_primary=True):
    """重写用户负责的工序列表（多对多）。
    station_ids 为空列表 = 清空负责工序；None = 不动（用于编辑时省略字段）。"""
    if station_ids is None:
        return
    db.query(M.UserStation).filter(M.UserStation.user_id == user_id).delete()
    for sid in station_ids:
        try:
            sid = int(sid)
        except (TypeError, ValueError):
            continue
        db.add(M.UserStation(user_id=user_id, station_id=sid))


def _save_user_perms(db, user, view_pages=None, manage_modules=None, present=None):
    """保存账号级自定义权限（仅当字段显式出现时处理）。
    view_pages 语义（present 里出现才算）：
      None → 清空自定义，回到角色默认模板
      []   → 严格空：一个页面都不给看
      其它数组 → 自定义页面集
    present: 本次请求显式出现的字段名集合（pydantic model_fields_set）"""
    if present is None:
        present = set()
    if "view_pages" in present:
        user.view_pages = json.dumps(view_pages, ensure_ascii=False) if view_pages is not None else None
    if "manage_modules" in present:
        user.manage_modules = json.dumps(manage_modules, ensure_ascii=False) if manage_modules is not None else None


def _user_row(db, x):
    return {"id": x.id, "username": x.username, "real_name": x.real_name,
            "department": x.department, "role": x.role_key,
            "role_name": ROLE_NAMES.get(x.role_key, x.role_key),
            "station_id": x.station_id,
            "station_ids": get_user_stations(db, x.id) or ([x.station_id] if x.station_id else []),
            "view_pages": _load_json_list(x.view_pages),
            "manage_modules": _load_json_list(x.manage_modules),
            "perm_custom": _load_json_list(x.view_pages) is not None or _load_json_list(x.manage_modules) is not None,
            "enabled": x.enabled}


class UserIn(BaseModel):
    username: str
    real_name: str
    password: str = "123456"
    department: str = ""
    role_key: str = "qc"
    station_id: int | None = None
    station_ids: list = []        # 负责工序（多选）
    view_pages: list | None = None    # None=按角色默认；[]=只读；[...]自定义
    manage_modules: list | None = None


@app.get("/api/admin/users")
def admin_users(keyword: str = "", token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "user")
    require_manage(u, "user")
    q = db.query(M.User)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(M.User.username.like(like), M.User.real_name.like(like)))
    rows = q.order_by(M.User.id).all()
    return [_user_row(db, x) for x in rows]


@app.post("/api/admin/users")
def admin_create_user(body: UserIn, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "user"); require_manage(u, "user")
    if db.query(M.User).filter(M.User.username == body.username.strip()).first():
        raise HTTPException(400, "用户名已存在")
    if body.role_key not in ROLE_PAGES:
        raise HTTPException(400, f"未知角色: {body.role_key}")
    salt, h = hash_pwd(body.password)
    nu = M.User(username=body.username.strip(), real_name=body.real_name.strip(),
                department=body.department.strip(), role_key=body.role_key,
                station_id=body.station_id, password_salt=salt, password_hash=h)
    db.add(nu); db.flush()
    _save_user_stations(db, nu.id, body.station_ids if body.station_ids else
                        ([body.station_id] if body.station_id else []))
    _save_user_perms(db, nu, body.view_pages, body.manage_modules,
                     present=body.model_fields_set)
    db.commit()
    audit(db, u.username, "create", f"user:{body.username}", "新建账号")
    db.commit()
    return {"ok": True, "id": nu.id}


class UserEditIn(BaseModel):
    real_name: str | None = None
    department: str | None = None
    role_key: str | None = None
    station_id: int | None = None
    station_ids: list | None = None
    view_pages: list | None = None
    manage_modules: list | None = None
    enabled: bool | None = None


@app.put("/api/admin/users/{uid}")
def admin_edit_user(uid: int, body: UserEditIn, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "user"); require_manage(u, "user")
    m = db.get(M.User, uid)
    if not m:
        raise HTTPException(404, "用户不存在")
    if body.real_name is not None: m.real_name = body.real_name
    if body.department is not None: m.department = body.department
    if body.role_key is not None:
        if body.role_key not in ROLE_PAGES:
            raise HTTPException(400, f"未知角色: {body.role_key}")
        m.role_key = body.role_key
    if body.station_id is not None: m.station_id = body.station_id
    if body.station_ids is not None:
        _save_user_stations(db, uid, body.station_ids)
        # 主工序与负责工序列表同步：取第一个为首工序；列表清空则主工序也清
        if "station_ids" in body.model_fields_set:
            m.station_id = body.station_ids[0] if body.station_ids else None
    if "view_pages" in body.model_fields_set or "manage_modules" in body.model_fields_set:
        _save_user_perms(db, m, body.view_pages, body.manage_modules,
                         present=body.model_fields_set)
    if body.enabled is not None:
        if m.username == "admin" and not body.enabled:
            raise HTTPException(400, "不能停用内置管理员")
        m.enabled = body.enabled
    db.commit()
    audit(db, u.username, "update", f"user:{uid}", "修改账号")
    db.commit()
    return {"ok": True}


class PwdIn(BaseModel):
    password: str


@app.put("/api/admin/users/{uid}/password")
def admin_reset_pwd(uid: int, body: PwdIn, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "user"); require_manage(u, "user")
    m = db.get(M.User, uid)
    if not m:
        raise HTTPException(404, "用户不存在")
    if len(body.password) < 4:
        raise HTTPException(400, "密码至少 4 位")
    m.password_salt, m.password_hash = hash_pwd(body.password)
    db.commit()
    audit(db, u.username, "update", f"user:{uid}", "重置密码")
    db.commit()
    return {"ok": True}


# ═══════════════════════ 总览统计 ═══════════════════════
@app.get("/api/overview")
def overview(token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    def cnt(m): return db.query(m).filter(m.enabled == True).count()
    return {
        "material": cnt(M.Material), "supplier": cnt(M.Supplier), "customer": cnt(M.Customer),
        "workshop": cnt(M.Workshop), "station": cnt(M.Station), "team": cnt(M.Team),
        "equipment": cnt(M.Equipment), "user": db.query(M.User).count(),
    }


# ═══════════════════════ Excel 导入 / 模板 ═══════════════════════
IMPORT_TEMPLATES = {
    # 文件名, 表头行(中文列名)
    "material":  ["编码", "名称", "类型(原料/辅料/中间品/成品)", "规格", "单位", "备注"],
    "supplier":  ["编码", "名称", "联系人", "电话", "备注"],
    "customer":  ["编码", "名称", "联系人", "电话", "备注"],
    "workshop":  ["编码", "名称", "备注"],
    "team":      ["名称", "班组长", "备注"],
}


@app.get("/api/templates/{entity}.csv")
def download_template(entity: str, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, entity)
    if entity not in IMPORT_TEMPLATES:
        raise HTTPException(404, "该模块暂不支持 Excel 导入")
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(IMPORT_TEMPLATES[entity])
    data = buf.getvalue()
    fname = urllib.parse.quote(f"{entity}_模板.csv")
    return Response(
        "\ufeff" + data, media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


@app.post("/api/import/{entity}")
async def import_entity(entity: str, file: UploadFile = File(...),
                        token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_manage(u, entity)
    if entity not in IMPORT_TEMPLATES:
        raise HTTPException(404, "该模块暂不支持 Excel 导入")
    raw = await file.read()
    cols = ENTITY_COLS[entity]
    rows = []
    if file.filename.lower().endswith(".xlsx"):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(raw))
        ws = wb.active
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r and any(v is not None and str(v).strip() for v in r):
                rows.append([str(v).strip() if v is not None else "" for v in r])
    else:  # csv
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("gbk", errors="replace")
        rd = csv.reader(io.StringIO(text))
        next(rd, None)  # 跳表头
        for r in rd:
            if any(v.strip() for v in r):
                rows.append([v.strip() for v in r])
    ok_n, errs, dup_n = 0, [], 0
    for i, r in enumerate(rows, start=2):
        data = dict(zip(cols, (r + [""] * len(cols))[:len(cols)]))
        try:
            if entity in ("station", "equipment"):
                raise HTTPException(400, "工序/设备请用页面新增（涉及关联选择）")
            if entity in ("material", "supplier", "customer", "workshop"):
                code = data.get("code")
                if not code:
                    errs.append(f"第{i}行缺编码"); continue
                if _find_conflict(db, ENTITY_MODEL[entity], "code", code):
                    dup_n += 1; continue
            if entity == "team":
                data = {k: data.get(k) for k in ("name", "leader", "remark")}
                if not data.get("name"):
                    errs.append(f"第{i}行缺名称"); continue
                if db.query(M.Team).filter(M.Team.name == data["name"]).first():
                    dup_n += 1; continue
            m = ENTITY_MODEL[entity](**data)
            db.add(m); ok_n += 1
        except HTTPException as e:
            errs.append(f"第{i}行: {e.detail}")
        except Exception as e:
            errs.append(f"第{i}行: {e}")
    db.commit()
    audit(db, u.username, "import", entity, f"成功{ok_n} 重复{dup_n} 失败{len(errs)}")
    db.commit()
    return {"ok": True, "total": len(rows), "inserted": ok_n, "duplicated": dup_n,
            "errors": errs[:20], "error_count": len(errs)}


# ═══════════════════════ 静态页 & 启动 ═══════════════════════
@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/health")
def health():
    return {"ok": True, "ts": datetime.now().isoformat()}


if __name__ == "__main__":
    import uvicorn
    print("=" * 46)
    print("  生产质量管理系统 · 第1步地基")
    print("  打开浏览器访问: http://localhost:8000")
    print("  演示账号: admin / qm / qc / prodlead 等, 密码 123456")
    print("=" * 46)
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="warning")
