# -*- coding: utf-8 -*-
"""生产质量管理系统 · 第1步：地基
登录/令牌 + 角色权限 + 基础资料 CRUD（物料/供应商/客户/车间/工序/班组/设备）
+ Excel 导入/模板下载 + 账号管理 + 审计日志
启动: python main.py  → http://localhost:8000
"""
import csv, hashlib, io, json, os, re, secrets, sys, uuid, urllib.parse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, Depends, HTTPException, UploadFile, File, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from database import Base, engine, SessionLocal, get_db, ensure_schema
import models as M

ensure_schema()

app = FastAPI(title="生产质量管理系统")
# CORS：默认只允许本机（前端与接口同源，本来不需要跨域）；公网部署要放别的域名时用环境变量指定
_ALLOW_ORIGINS = [x.strip() for x in os.environ.get(
    "QMS_ALLOW_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000").split(",") if x.strip()]
app.add_middleware(CORSMiddleware, allow_origins=_ALLOW_ORIGINS, allow_methods=["*"],
                   allow_headers=["*"])

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(STATIC_DIR, exist_ok=True)

TOKEN_TTL_HOURS = 12

# ═══════════════════════ 角色权限矩阵 ═══════════════════════
# 页面 key → (菜单显示名, 分组, 图标)
PAGES = {
    "dashboard":   ("📊 质量总览", "总览", ""),
    "screen":      ("🖥️ 车间大屏", "总览", "screen"),
    "board":       ("📺 数据大屏", "总览", "board"),
    "prodlot":     ("🏭 生产批次", "生产制造", "prodlot"),
    "qcstandard":  ("📋 检验标准库", "质量管控", "qcstandard"),
    "incoming":    ("🚚 来料检验", "质量管控", "incoming"),
    "ncr":         ("⚠️ 不合格处理", "质量管控", "ncr"),
    "trace":       ("🔍 批次追溯", "质量管控", "trace"),
    "report":      ("📈 统计报表", "质量管控", "report"),
    "complaint":   ("📣 客诉管理", "质量管控", "complaint"),
    "material":    ("📦 物料管理", "基础资料", "material"),
    "supplier":    ("🚚 供应商", "基础资料", "supplier"),
    "customer":    ("🤝 客户", "基础资料", "customer"),
    "workshop":    ("🏭 车间管理", "基础资料", "workshop"),
    "station":     ("⚙️ 工序管理", "基础资料", "station"),
    "team":        ("👥 班组管理", "基础资料", "team"),
    "equipment":   ("🔧 设备管理", "基础资料", "equipment"),
    "department":  ("🏢 部门管理", "基础资料", "department"),
    "position":    ("👔 职务管理", "基础资料", "position"),
    "user":        ("🛠️ 账号管理", "系统管理", "user"),
    "duty":        ("🔐 岗位职责", "系统管理", "duty"),
    "audit":       ("📜 操作日志", "系统管理", "audit"),
}
_QC_CORE = ["dashboard", "board", "screen", "prodlot", "qcstandard", "incoming", "ncr",
            "trace", "report", "complaint"]
_BASEINFO = ["material", "supplier", "customer", "workshop", "station", "team", "equipment",
             "department", "position"]
# 角色 → 可访问页面
ROLE_PAGES = {
    "admin":     list(PAGES.keys()),
    "boss":      _QC_CORE + _BASEINFO,
    "qm":        _QC_CORE + _BASEINFO,
    "qc":        ["dashboard", "screen", "prodlot", "qcstandard", "incoming", "ncr", "trace",
                  "report", "complaint", "material", "equipment"],
    "sampler":   ["dashboard", "incoming", "material"],
    "prodlead":  ["dashboard", "screen", "prodlot", "trace", "qcstandard", "workshop", "station", "equipment", "team"],
    "buyer":     ["dashboard", "incoming", "ncr", "trace", "complaint", "material", "supplier", "customer"],
    "store":     ["dashboard", "incoming", "ncr", "trace", "material", "customer", "workshop"],
    "worker":    ["dashboard", "prodlot"],
}
# 角色 → 可管理(增删改)的页面 key；不在列表 = 只读/仅查看
ROLE_MANAGE = {
    "admin":   ["material", "supplier", "customer", "workshop", "station", "team", "equipment",
                "department", "position", "user", "duty", "audit", "qcstandard", "incoming", "ncr",
                "prodlot", "complaint"],
    "qm":      ["material", "supplier", "customer", "workshop", "station", "team", "equipment",
                "department", "position", "qcstandard", "incoming", "ncr", "prodlot", "complaint"],
    "prodlead": ["prodlot"],
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
    "department": M.Department,
    "position":   M.Position,
}
ENTITY_COLS = {
    "material":  ["code", "name", "material_type", "spec", "unit", "remark"],
    "supplier":  ["code", "name", "contact", "phone", "remark"],
    "customer":  ["code", "name", "contact", "phone", "remark"],
    "workshop":  ["code", "name", "remark"],
    "station":   ["code", "name", "workshop_id", "seq", "remark"],
    "team":      ["name", "leader", "remark"],
    "equipment": ["code", "name", "station_id", "category", "remark"],
    "department": ["code", "name", "remark"],
    "position":   ["code", "name", "remark"],
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
    u = db.query(M.User).filter(M.User.id == row.user_id, M.User.enabled == True).first()
    return attach_duty(u, db)


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


def _duty_of(db, dept_id, position_id):
    """按 部门+职务 找岗位职责模板：优先精确(部门+职务)，其次通用职务(部门为空)"""
    if not position_id:
        return None
    q = db.query(M.DutyTemplate).filter(M.DutyTemplate.position_id == position_id,
                                        M.DutyTemplate.enabled == True)
    if dept_id:
        d = q.filter(M.DutyTemplate.dept_id == dept_id).first()
        if d:
            return d
    return q.filter(M.DutyTemplate.dept_id.is_(None)).first()


def attach_duty(user, db):
    """把 部门+职务 的职责模板解析结果挂到 user 上（user_pages/user_manage 无需再查库）"""
    if user is None:
        return user
    d = _duty_of(db, getattr(user, "dept_id", None), getattr(user, "position_id", None))
    user._duty_view = _load_json_list(d.view_pages) if d else None
    user._duty_manage = _load_json_list(d.manage_modules) if d else None
    return user


def perm_source(user) -> str:
    """权限来源：account(账号微调) / duty(部门+职务) / role(角色默认)"""
    if _load_json_list(getattr(user, "view_pages", None)) is not None or \
       _load_json_list(getattr(user, "manage_modules", None)) is not None:
        return "account"
    if getattr(user, "_duty_view", None) is not None or getattr(user, "_duty_manage", None) is not None:
        return "duty"
    return "role"


def user_pages(user) -> list:
    """有效页面权限：账号自定义 > 部门+职务职责模板 > 角色默认"""
    custom = _load_json_list(getattr(user, "view_pages", None))
    if custom is not None:
        return [k for k in custom if k in PAGES]
    duty = getattr(user, "_duty_view", None)
    if duty is not None:
        return [k for k in duty if k in PAGES]
    return ROLE_PAGES.get(user.role_key, ["dashboard"])


def user_manage(user) -> list:
    """有效管理权限：账号自定义 > 部门+职务职责模板 > 角色默认"""
    custom = _load_json_list(getattr(user, "manage_modules", None))
    if custom is not None:
        return [k for k in custom if k in PAGES]
    duty = getattr(user, "_duty_manage", None)
    if duty is not None:
        return [k for k in duty if k in PAGES]
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


# ── 登录防爆破（公网暴露必备）：同一账号+IP 连错 5 次锁 10 分钟；
#    同一 IP 连错 20 次（换账号猜）锁 30 分钟。重启服务即清空。
LOGIN_MAX_FAILS = int(os.environ.get("QMS_LOGIN_MAX_FAILS", "5"))
LOGIN_LOCK_MINUTES = int(os.environ.get("QMS_LOGIN_LOCK_MINUTES", "10"))
IP_MAX_FAILS = int(os.environ.get("QMS_IP_MAX_FAILS", "20"))
IP_LOCK_MINUTES = int(os.environ.get("QMS_IP_LOCK_MINUTES", "30"))
_LOGIN_FAILS = {}      # "用户名|IP" -> [连续失败次数, 锁定到期时间]
_IP_FAILS = {}         # "IP" -> [连续失败次数, 锁定到期时间]


def _client_ip(request):
    if request is None:
        return "?"
    # 经 cpolar/nginx 等反代时取真实客户端 IP
    for h in ("x-forwarded-for", "x-real-ip"):
        v = request.headers.get(h)
        if v:
            return v.split(",")[0].strip()
    return request.client.host if request.client else "?"


def _locked_until(bucket, key):
    it = bucket.get(key)
    if not it:
        return None
    if it[1] and datetime.now() < it[1]:
        return it[1]
    if it[1] and datetime.now() >= it[1]:
        bucket.pop(key, None)
    return None


def _note_fail(bucket, key, max_fails, lock_minutes):
    it = bucket.get(key, [0, None])
    it[0] = (it[0] or 0) + 1
    if it[0] >= max_fails:
        it[1] = datetime.now() + timedelta(minutes=lock_minutes)
        it[0] = 0
    bucket[key] = it
    return bool(it[1])


@app.post("/api/auth/login")
def login(body: LoginIn, request: Request = None, db: Session = Depends(get_db)):
    uname = (body.username or "").strip()
    ip = _client_ip(request)
    key, ipkey = f"{uname}|{ip}", ip
    # ① 账号+IP 锁定
    until = _locked_until(_LOGIN_FAILS, key)
    if until:
        mins = max(1, int((until - datetime.now()).total_seconds() // 60) + 1)
        raise HTTPException(429, f"密码连续错误次数过多，请 {mins} 分钟后再试")
    # ② 同 IP 换账号猜
    until2 = _locked_until(_IP_FAILS, ipkey)
    if until2:
        mins = max(1, int((until2 - datetime.now()).total_seconds() // 60) + 1)
        raise HTTPException(429, f"该网络登录失败次数过多，请 {mins} 分钟后再试")
    u = db.query(M.User).filter(M.User.username == uname).first()
    if not u or not check_pwd(body.password, u.password_salt, u.password_hash):
        db.add(M.AuditLog(username=uname or "(空)", action="login_fail",
                          target=f"ip:{ip}", detail="密码错误"))
        db.commit()
        locked = _note_fail(_LOGIN_FAILS, key, LOGIN_MAX_FAILS, LOGIN_LOCK_MINUTES)
        _note_fail(_IP_FAILS, ipkey, IP_MAX_FAILS, IP_LOCK_MINUTES)
        if locked:
            raise HTTPException(429, f"密码连续错误 {LOGIN_MAX_FAILS} 次，账号已锁定 "
                                     f"{LOGIN_LOCK_MINUTES} 分钟")
        raise HTTPException(401, "用户名或密码错误")
    if not u.enabled:
        raise HTTPException(403, "账号已停用，请联系管理员")
    _LOGIN_FAILS.pop(key, None)
    token = uuid.uuid4().hex
    db.add(M.AuthToken(token=token, user_id=u.id,
                       expires_at=datetime.now() + timedelta(hours=TOKEN_TTL_HOURS)))
    db.add(M.AuditLog(username=u.username, action="login", target=f"ip:{ip}", detail="登录成功"))
    db.commit()
    attach_duty(u, db)
    pm = page_meta(u)
    _pos = db.get(M.Position, u.position_id) if u.position_id else None
    _dep = db.get(M.Department, u.dept_id) if u.dept_id else None
    return {"token": token, "user": {
        "id": u.id, "username": u.username, "real_name": u.real_name,
        "department": u.department, "dept_id": u.dept_id,
        "dept_name": _dep.name if _dep else u.department,
        "position_id": u.position_id, "position_name": _pos.name if _pos else "",
        "role": u.role_key, "station_id": u.station_id,
        "station_ids": get_user_stations(db, u.id) or ([u.station_id] if u.station_id else []),
        "perm_custom": _load_json_list(u.view_pages) is not None or _load_json_list(u.manage_modules) is not None,
        "perm_source": perm_source(u),
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
    if entity == "department":
        if db.query(M.User).filter(M.User.dept_id == eid, M.User.enabled == True).first():
            raise HTTPException(400, "该部门下还有账号，不能删除（可先调整人员或停用账号）")
        if db.query(M.DutyTemplate).filter(M.DutyTemplate.dept_id == eid,
                                           M.DutyTemplate.enabled == True).first():
            raise HTTPException(400, "该部门还配着岗位职责，请先在「岗位职责」里停用")
    if entity == "position":
        if db.query(M.User).filter(M.User.position_id == eid, M.User.enabled == True).first():
            raise HTTPException(400, "该职务下还有账号，不能删除（可先调整人员）")
        if db.query(M.DutyTemplate).filter(M.DutyTemplate.position_id == eid,
                                           M.DutyTemplate.enabled == True).first():
            raise HTTPException(400, "该职务还配着岗位职责，请先在「岗位职责」里停用")
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
CHECK_BY_NAMES = {"self": "车间自检", "dept": "质检部检测"}
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
                       "is_key": it.is_key, "check_by": it.check_by or "dept"} for it in items]
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
        _by = it.get("check_by") if it.get("check_by") in ("self", "dept") else "dept"
        if o:
            o.seq = i; o.unit = it.get("unit", ""); o.min_val = lo; o.max_val = hi
            o.method = it.get("method", ""); o.is_key = 1 if it.get("is_key") else 0
            o.check_by = _by
        else:
            db.add(M.QcStandardItem(standard_id=sid, seq=i, indicator=indicator,
                                    unit=it.get("unit", ""), min_val=lo, max_val=hi,
                                    method=it.get("method", ""),
                                    is_key=1 if it.get("is_key") else 0, check_by=_by))
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
              "max_val": it.max_val, "method": it.method, "is_key": it.is_key, "check_by": it.check_by}
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
                       "method": it.method, "is_key": it.is_key, "check_by": it.check_by} for it in items]}


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


def _judge_and_fill(db, tr, std_items, body_items, only_by=None):
    """通用：按标准逐项判定并写入 TestItem。返回 (fail_parts, fail_n, judged_n)
    三类检验（来料/过程/成品）共用，保证判定逻辑只有一份。
    only_by: 只判定指定检测方（{'self'} 车间自检 / {'dept'} 质检部），None=不限"""
    fail_parts, fail_n, judged_n = [], 0, 0
    for seq, it in enumerate(body_items, start=1):
        indicator = (it.get("indicator") or "").strip()
        actual = str(it.get("actual", "")).strip()
        if not indicator:
            continue
        s = std_items.get(indicator)
        if not s:
            continue
        if only_by and (getattr(s, "check_by", "dept") or "dept") not in only_by:
            raise HTTPException(400, f"指标「{indicator}」不由你所在环节检测（{CHECK_BY_NAMES.get(getattr(s, 'check_by', 'dept'), '')}），请交对应环节录入")
        judged_n += 1
        lo, hi = s.min_val, s.max_val
        if s.min_val is None and s.max_val is None:
            passed = actual != ""          # 外观等文本项：填了就算过（人工目测）
            note = ""
        else:
            passed, note = _judge_value(actual, lo, hi)
        db.add(M.TestItem(test_id=tr.id, seq=seq, indicator=indicator, unit=s.unit,
                          min_val=lo, max_val=hi, method=s.method, is_key=s.is_key,
                          check_by=getattr(s, "check_by", "dept") or "dept",
                          actual=actual, pass_flag=1 if passed else 0))
        if not passed:
            fail_n += 1
            lim = []
            if lo is not None: lim.append(f"≥{lo}")
            if hi is not None: lim.append(f"≤{hi}")
            fail_parts.append(f"{indicator} 实测{actual}（标准{'/'.join(lim) or s.unit}）{note}")
    return fail_parts, fail_n, judged_n


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
    fail_parts, fail_n, judged_n = _judge_and_fill(db, tr, std_items, body.items)
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
        # 来料检验挂 lot_id；生产批（过程/成品）检验挂 prod_id → 分别取名，避免空主键查询
        lot = (tr.lot or db.get(M.IncomingLot, tr.lot_id)) if tr.lot_id else None
        prod = (tr.prod or db.get(M.ProductionLot, tr.prod_id)) if tr.prod_id else None
        if lot:
            m = db.get(M.Material, lot.material_id) if lot.material_id else None
            obj_no = lot.lot_no
            mat_name = f"{m.name}（{m.code}）" if m else ""
        elif prod:
            m = db.get(M.Material, prod.material_id) if prod.material_id else None
            st = db.get(M.Station, prod.station_id) if prod.station_id else None
            obj_no = prod.lot_no
            mat_name = (f"{m.name}（{m.code}）·" if m else "") + (st.name if st else "")
        else:
            obj_no, mat_name = "", ""
        items = db.query(M.TestItem).filter(M.TestItem.test_id == tr.id) \
            .order_by(M.TestItem.seq).all()
        out.append({
            "id": tr.id, "test_no": tr.test_no, "lot_no": obj_no,
            "material_name": mat_name,
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
        # 关键字搜索：来料批号 or 生产批号（LEFT 各自匹配）
        q = q.outerjoin(M.IncomingLot).outerjoin(M.ProductionLot).filter(
            M.IncomingLot.lot_no.like(f"%{keyword}%") | M.ProductionLot.lot_no.like(f"%{keyword}%"))
    rows = q.order_by(M.Ncr.id.desc()).limit(200).all()
    out = []
    for ncr in rows:
        lot = ncr.lot or db.get(M.IncomingLot, ncr.lot_id) if ncr.lot_id else None
        prod = ncr.prod or db.get(M.ProductionLot, ncr.prod_id) if ncr.prod_id else None
        m = None
        lot_no, mat_name = "", ""
        if lot:
            lot_no = lot.lot_no
            m = db.get(M.Material, lot.material_id) if lot.material_id else None
            mat_name = m.name if m else ""
        elif prod:
            lot_no = prod.lot_no
            m = db.get(M.Material, prod.material_id) if prod.material_id else None
            mat_name = f"{m.name}（{m.code}）" if m else ""
            # 生产批 NCR：显示工序来源更直观
            st = db.get(M.Station, prod.station_id) if prod.station_id else None
            if st:
                mat_name = f"{mat_name}·{st.name}" if mat_name else st.name
        out.append({"id": ncr.id, "ncr_no": ncr.ncr_no,
                    "lot_no": lot_no,
                    "material_name": mat_name,
                    "source": "incoming" if lot else "production",
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
    # 权限矩阵：来料 NCR 按原规则；生产批 NCR 没有"退货"，reject 等价报废(冻结)，
    # waive(让步放行)与 scrap(报废) 仅质量经理
    lot_status, prod_status, ncr_status = None, None, None
    if ncr.prod_id:
        if d == "reject":
            # 生产批不退货：采购无权处置生产批不合格，须质量经理
            _require_action(u, ["admin", "qm"])
            prod_status, ncr_status = 3, 3          # 保持冻结=报废处置
        elif d == "waive":
            _require_action(u, ["admin", "qm"])
            prod_status, ncr_status = None, 2       # 让步放行（回合格态由前端指定/默认2）
        else:
            _require_action(u, ["admin", "qm"])
            prod_status, ncr_status = 3, 3
    else:
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
    if ncr.lot_id:
        lot = db.get(M.IncomingLot, ncr.lot_id)
        if lot:
            lot.status = lot_status
            lot.updated_at = datetime.now()
    if ncr.prod_id:
        prod = db.get(M.ProductionLot, ncr.prod_id)
        if prod:
            # waive=让步放行回可用(2 过程合格或 5 成品合格由原状态决定)；scrap/reject 保持冻结
            if d == "waive":
                prod.status = 5 if prod.status == 6 else 2
            prod.updated_at = datetime.now()
    db.commit()
    audit(db, u.username, "update", f"ncr:{ncr.ncr_no}",
          f"处置={d} {body.remark.strip()}")
    db.commit()
    return {"ok": True, "lot_status": lot_status, "ncr_status": ncr_status}

# ═══════════════════════ 生产批次/过程检验/成品检验（第4步）═══════════════════════
PROD_STATUS = {1: "待过程检验", 2: "过程合格", 3: "过程不合格冻结",
               4: "待成品检验", 5: "成品合格", 6: "成品不合格冻结"}
COA_CHECK_TYPES = {"ipqc": "过程检验", "oqc": "成品检验"}


def _prod_out(db, p):
    st = db.get(M.Station, p.station_id)
    eq = db.get(M.Equipment, p.equipment_id) if p.equipment_id else None
    team = db.get(M.Team, p.team_id) if p.team_id else None
    mat = db.get(M.Material, p.material_id) if p.material_id else None
    coa = db.query(M.Coa).filter(M.Coa.prod_id == p.id).order_by(M.Coa.id.desc()).first()
    latest = db.query(M.TestRecord).filter(M.TestRecord.prod_id == p.id) \
        .order_by(M.TestRecord.id.desc()).first()
    ncr = db.query(M.Ncr).filter(M.Ncr.prod_id == p.id).order_by(M.Ncr.id.desc()).first()
    return {
        "pending_dept": p.pending_dept or 0,

        "id": p.id, "lot_no": p.lot_no,
        "station_id": p.station_id,
        "station_name": st.name if st else "",
        "station_code": st.code if st else "",
        "equipment_id": p.equipment_id,
        "equipment_name": eq.name if eq else "",
        "team_name": team.name if team else "",
        "material_id": p.material_id,
        "material_name": f"{mat.name}（{mat.code}）" if mat else "",
        "material_type": mat.material_type if mat else "",
        "parent_type": p.parent_type, "parent_lot_no": p.parent_lot_no,
        "qty": p.qty, "unit": p.unit, "status": p.status,
        "status_name": PROD_STATUS.get(p.status, str(p.status)),
        "operator": p.operator,
        "remark": p.remark,
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "",
        "coa_no": coa.coa_no if coa else None,
        "latest_result": latest.result if latest else None,
        "ncr_no": ncr.ncr_no if ncr else None,
    }


def _prev_stations(db, station_id):
    """上工序：next_station_id == 本工序的工序（父批必须是它产的合格批）"""
    return [st.id for st in db.query(M.Station).filter(M.Station.next_station_id == station_id).all()]


def _usable_parent_check(db, lot, station_id, parent_type, parent_lot_no):
    """父批可用性校验（第4步核心防错）：
    首工序（无上工序）→ 父批必须是合格/让步的原料批；
    有上工序 → 父批必须是上工序产出的过程合格生产批。"""
    prevs = _prev_stations(db, station_id)
    if not prevs:
        # 首工序：父 = 原料批（合格放行 2 / 让步接收 4）
        src = db.query(M.IncomingLot).filter(M.IncomingLot.lot_no == parent_lot_no).first()
        if not src:
            raise HTTPException(400, "父批不存在（首工序父批须为已登记的原料批号）")
        if src.status not in (2, 4):
            raise HTTPException(400, f"父原料批当前状态为{LOT_STATUS.get(src.status)}，须为合格放行或让步接收")
        return src
    # 有上工序：父 = 上工序产出的生产批（过程合格 2 / 成品合格 5 均可继续加工）
    src = db.query(M.ProductionLot).filter(M.ProductionLot.lot_no == parent_lot_no).first()
    if not src:
        raise HTTPException(400, "父批不存在（该工序父批须为上工序的生产批号）")
    if src.station_id not in prevs:
        raise HTTPException(400, "父批不是本工序的上一道工序产出，禁止引用")
    if src.status not in (2, 5):
        raise HTTPException(400, f"父生产批当前状态为{PROD_STATUS.get(src.status)}，须为合格批次")
    return src


class ProdLotIn(BaseModel):
    station_id: int
    equipment_id: int | None = None
    team_id: int | None = None
    material_id: int | None = None
    parent_type: str = ""         # incoming / production
    parent_lot_no: str = ""
    qty: float = 0
    remark: str = ""


@app.get("/api/production-lots")
def prod_list(status: str = "", keyword: str = "", station_id: str = "",
              token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "prodlot")
    q = db.query(M.ProductionLot)
    if status != "" and status is not None:
        q = q.filter(M.ProductionLot.status == int(status))
    if station_id != "" and station_id is not None:
        q = q.filter(M.ProductionLot.station_id == int(station_id))
    if keyword:
        q = q.filter(M.ProductionLot.lot_no.like(f"%{keyword}%"))
    rows = q.order_by(M.ProductionLot.id.desc()).limit(300).all()
    return [_prod_out(db, p) for p in rows]


@app.post("/api/production-lots")
def prod_create(body: ProdLotIn, token: str = Header(""), db: Session = Depends(get_db)):
    """班组长建批（=完工登记）：选 工序/设备/班组/产出物料/父批。
    批号：YYYYMMDD-STXX-EQXX-NNN-[父批]-班组（沿用化工溯源规则）"""
    u = require_user(token, db)
    require_page(u, "prodlot")
    _require_action(u, ["admin", "qm", "prodlead"])
    st = db.get(M.Station, body.station_id)
    if not st:
        raise HTTPException(400, "工序不存在")
    if body.equipment_id:
        eq = db.get(M.Equipment, body.equipment_id)
        if not eq or eq.station_id != st.id:
            raise HTTPException(400, "设备不存在或不属于该工序")
    parent_src = _usable_parent_check(db, None, st.id, body.parent_type, body.parent_lot_no.strip())
    if not body.material_id and parent_src:
        # 默认产出 = 首工序父原料本身；非首工序必须显式（多为中间料，无物料档案则留空）
        pass
    team = db.get(M.Team, body.team_id) if body.team_id else None
    today = datetime.now().strftime("%Y%m%d")
    st_code = st.code if st.code.startswith("ST") else "ST" + st.code
    eq_code = (db.get(M.Equipment, body.equipment_id).code
               if body.equipment_id else "NA")
    eq_tail = "".join(ch for ch in eq_code if ch.isdigit())[-2:].zfill(2)
    st_tail = st_code[-2:]
    prefix = f"{today}-ST{st_tail}-EQ{eq_tail}-"
    # 序号按 当天+工序 顺延
    rows = db.query(M.ProductionLot).filter(M.ProductionLot.lot_no.like(prefix + "%")).all()
    mx = 0
    for r in rows:
        tail = r.lot_no[len(prefix):]
        try:
            mx = max(mx, int(tail.split("-")[0]))
        except ValueError:
            pass
    seq = mx + 1
    team_short = team.name.replace("班", "")[:2] if team else "X"
    m = re.search(r"(ST\d+-EQ\d+-\d{3}|RA-[A-Z0-9]+-\d{3})", parent_src.lot_no or "")
    short_ref = m.group(1) if m else (parent_src.lot_no or "")[-16:]
    lot_no = f"{prefix}{seq:03d}-[{short_ref}]-{team_short}"
    p = M.ProductionLot(lot_no=lot_no, station_id=st.id,
                        equipment_id=body.equipment_id, team_id=body.team_id,
                        material_id=body.material_id,
                        parent_type="incoming" if isinstance(parent_src, M.IncomingLot) else "production",
                        parent_lot_no=parent_src.lot_no,
                        qty=body.qty, unit="kg", status=1,
                        operator=u.username, remark=body.remark.strip())
    db.add(p); db.commit()
    audit(db, u.username, "create", f"prodlot:{lot_no}", f"完工登记 {st.name} {body.qty}kg")
    db.commit()
    return {"ok": True, "id": p.id, "lot_no": lot_no}


@app.get("/api/production-lots/{pid}/test-form")
def prod_test_form(pid: int, check_type: str = "ipqc", token: str = Header(""),
                   db: Session = Depends(get_db)):
    """检验模板：ipqc→工序标准；oqc→产出成品物料标准"""
    u = require_user(token, db)
    require_page(u, "prodlot")
    p = db.get(M.ProductionLot, pid)
    if not p:
        raise HTTPException(404, "生产批不存在")
    std = None
    if check_type == "oqc":
        if not p.material_id:
            raise HTTPException(400, "该批未登记产出成品物料，无法做成品检验")
        mat = db.get(M.Material, p.material_id)
        if not mat or mat.material_type != "成品":
            raise HTTPException(400, f"产出物料 {mat.name if mat else '?'} 不是成品，无需成品检验")
        std = db.query(M.QcStandard).filter(
            M.QcStandard.object_type == "material", M.QcStandard.object_id == p.material_id,
            M.QcStandard.check_type == "oqc", M.QcStandard.status == 1).first()
        if not std:
            raise HTTPException(400, f"成品 {mat.name} 还没有成品检验标准，请先在标准库配置")
    else:
        std = db.query(M.QcStandard).filter(
            M.QcStandard.object_type == "station", M.QcStandard.object_id == p.station_id,
            M.QcStandard.check_type == "ipqc", M.QcStandard.status == 1).first()
        if not std:
            raise HTTPException(400, "该工序还没有过程检验标准，请先在标准库配置")
    items = db.query(M.QcStandardItem).filter(
        M.QcStandardItem.standard_id == std.id, M.QcStandardItem.enabled == 1
    ).order_by(M.QcStandardItem.seq).all()
    n_self = len([x for x in items if (x.check_by or "dept") == "self"])
    prev = db.query(M.TestRecord).filter(
        M.TestRecord.prod_id == p.id, M.TestRecord.std_id == std.id,
        M.TestRecord.check_type == check_type).order_by(M.TestRecord.id.desc()).first()
    role = u.role_key
    return {"std_id": std.id, "std_no": std.std_no, "std_name": std.name,
            "check_type": check_type,
            "self_count": n_self, "dept_count": len(items) - n_self,
            "can_self": role in ("admin", "qm", "prodlead", "worker"),
            "can_dept": role in ("admin", "qm", "qc"),
            "self_done": bool(prev and prev.self_at),
            "dept_done": bool(prev and prev.dept_at),
            "test_no": prev.test_no if prev else "",
            "items": [{"indicator": it.indicator, "unit": it.unit,
                       "min_val": it.min_val, "max_val": it.max_val,
                       "method": it.method, "is_key": it.is_key,
                       "check_by": it.check_by or "dept"} for it in items]}


class ProdTestIn(BaseModel):
    std_id: int
    check_type: str = "ipqc"      # ipqc / oqc
    items: list
    stage: str = ""               # ""=整体判定(兼容/来料) / self=车间自检 / dept=质检部检测
    customer_id: int | None = None  # oqc 时可选：按客户出 COA


def _fail_summary(bad_items):
    """不合格摘要：逐项列出实测值并标注检测方来源（车间自检/质检部）"""
    return "；".join(
        f"{x.indicator} 实测{x.actual}"
        + ("（车间自检）" if (x.check_by or "dept") == "self" else "（质检部）")
        for x in bad_items[:8])


def _prod_ncr_no(db):
    return _next_no(db, M.Ncr, "ncr_no", f"NCR-{datetime.now().year}-")


@app.post("/api/production-lots/{pid}/test")
def prod_test(pid: int, body: ProdTestIn, token: str = Header(""),
              db: Session = Depends(get_db)):
    """过程/成品检验提交（第二批：按检测方分阶段，一张单分两块）
    stage=self 车间自检：车间角色可交，只判"自检"指标；全合格→可先流转，
               若还有质检部指标未录则挂"待质检确认"（关键项不挡生产，质检后置把关）
    stage=dept 质检部检测：质检角色可交，判"质检部"指标（与已有自检项合并成一张单）；
               有不合格→冻结+NCR；全合格→完成并推进（末道成品→待成品检验 / OQC→出COA）
    stage=""   整体判定（兼容老调用与来料检验）
    """
    u = require_user(token, db)
    require_page(u, "prodlot")
    p = db.get(M.ProductionLot, pid)
    if not p:
        raise HTTPException(404, "生产批不存在")
    ctype = body.check_type
    stage = (body.stage or "").strip().lower()
    if stage not in ("", "self", "dept"):
        raise HTTPException(400, "stage 须为 self/dept/空")
    std = db.get(M.QcStandard, body.std_id)
    if not std:
        raise HTTPException(400, "标准不存在")
    std_list = db.query(M.QcStandardItem).filter(
        M.QcStandardItem.standard_id == std.id, M.QcStandardItem.enabled == 1).all()
    std_items = {it.indicator: it for it in std_list}
    n_self = len([x for x in std_list if (x.check_by or "dept") == "self"])
    n_dept = len(std_list) - n_self

    # ── 环节 + 角色校验 ──
    if stage == "self":
        _require_action(u, ["admin", "qm", "prodlead", "worker"])
        if n_self == 0:
            raise HTTPException(400, "该工序标准没有配置「车间自检」指标，自检由质检部完成")
        only_by = {"self"}
    elif stage == "dept":
        _require_action(u, ["admin", "qm", "qc"])
        if n_dept == 0:
            raise HTTPException(400, "该工序标准全部为「车间自检」指标，无需质检部检测")
        only_by = {"dept"}
    else:
        _require_action(u, ["admin", "qm", "qc"])
        only_by = None

    # ── 批次状态校验 ──
    if ctype == "ipqc":
        allow = (1, 2) if stage == "dept" else (1,)
        if p.status not in allow:
            raise HTTPException(400, f"该批状态为{PROD_STATUS.get(p.status)}，不能提交过程检验")
    elif ctype == "oqc":
        if p.status != 4:
            raise HTTPException(400, f"该批状态为{PROD_STATUS.get(p.status)}，只有'待成品检验'可提交成品检验")
        if not p.material_id:
            raise HTTPException(400, "该批未登记产出物料")
    else:
        raise HTTPException(400, "check_type 须为 ipqc/oqc")

    # ── 一张单分两块：质检提交时复用自检那张单 ──
    tr = None
    if stage == "dept":
        tr = db.query(M.TestRecord).filter(
            M.TestRecord.prod_id == p.id, M.TestRecord.std_id == std.id,
            M.TestRecord.check_type == ctype, M.TestRecord.result == 0
        ).order_by(M.TestRecord.id.desc()).first()
    created = False
    if tr is None:
        today = datetime.now().strftime("%Y%m%d")
        tr = M.TestRecord(test_no=_next_no(db, M.TestRecord, "test_no", f"T-{today}-"),
                          prod_id=p.id, std_id=std.id, check_type=ctype,
                          result=0, tested_by=u.username)
        db.add(tr)
        db.flush()
        created = True
    fail_parts, fail_n, judged_n = _judge_and_fill(db, tr, std_items, body.items, only_by=only_by)
    if judged_n == 0:
        if created:
            db.delete(tr)
            db.commit()
        raise HTTPException(400, "没有可判定的检验项（请按模板逐项录入实测值）")
    if stage == "self":
        tr.self_by = u.username
        tr.self_at = datetime.now()
        tr.stage = 1
    elif stage == "dept":
        tr.dept_by = u.username
        tr.dept_at = datetime.now()

    # ── 汇总本单所有已录项（自检+质检）──
    db.flush()
    all_items = db.query(M.TestItem).filter(M.TestItem.test_id == tr.id).all()
    bad = [x for x in all_items if x.pass_flag == 0]
    got_dept = len([x for x in all_items if (x.check_by or "dept") == "dept"])
    ncr_no = None
    if bad:
        tr.result = 2
        p.status = 3 if ctype == "ipqc" else 6
        p.pending_dept = 0
        summary = _fail_summary(bad)
        ncr_no = _prod_ncr_no(db)
        db.add(M.Ncr(ncr_no=ncr_no, prod_id=p.id, test_id=tr.id, fail_summary=summary,
                     status=0, created_by=u.username))
        audit(db, u.username, "create", f"ncr:{ncr_no}",
              f"{COA_CHECK_TYPES.get(ctype)}不合格自动开单：{summary[:60]}")
    else:
        dept_pending = got_dept < n_dept
        if stage == "self" and dept_pending:
            # 自检合格即可先流转；关键（质检部）项未出结果 → 挂待质检
            tr.result = 0
            tr.stage = 1
            if ctype == "ipqc":
                p.status = 2
            p.pending_dept = 1
            audit(db, u.username, "update", f"prodlot:{p.lot_no}",
                  f"车间自检合格 {tr.test_no}（待质检部确认 {n_dept - got_dept} 项）")
        else:
            tr.result = 1
            tr.stage = 2
            p.pending_dept = 0
            if ctype == "ipqc":
                mat = db.get(M.Material, p.material_id) if p.material_id else None
                p.status = 4 if (mat and mat.material_type == "成品") else 2
                audit(db, u.username, "update", f"prodlot:{p.lot_no}",
                      f"过程检验合格 {tr.test_no}（自检{len([x for x in all_items if (x.check_by or 'dept')=='self'])}项/质检{n_dept}项）")
            else:
                p.status = 5
                _issue_coa(db, p, tr.id, body.customer_id, u.username)
                audit(db, u.username, "update", f"prodlot:{p.lot_no}", f"成品检验合格出COA {tr.test_no}")
    p.updated_at = datetime.now()
    db.commit()
    return {"ok": True, "test_no": tr.test_no, "result": tr.result, "stage": tr.stage,
            "pending_dept": p.pending_dept, "status": p.status,
            "fail_summary": _fail_summary(bad) if bad else "",
            "ncr_no": ncr_no}


def _issue_coa(db, prod_lot, test_id, customer_id, username):
    """OQC 合格 → 生成 COA（明细快照 items_json，此后标准改动不影响历史报告）"""
    db.flush()   # 关键：session autoflush=False，先落库才能查到本次检验明细
    items = db.query(M.TestItem).filter(M.TestItem.test_id == test_id) \
        .order_by(M.TestItem.seq).all()
    snap = [{"indicator": it.indicator, "actual": it.actual, "unit": it.unit,
             "min_val": it.min_val, "max_val": it.max_val, "pass": it.pass_flag,
             "check_by": it.check_by}
            for it in items]
    today = datetime.now().strftime("%Y%m%d")
    coa_no = _next_no(db, M.Coa, "coa_no", f"COA-{today}-")
    db.add(M.Coa(coa_no=coa_no, prod_id=prod_lot.id, customer_id=customer_id,
                 test_id=test_id, items_json=json.dumps(snap, ensure_ascii=False),
                 result=1, issued_by=username, access_key=secrets.token_hex(16)))
    return coa_no


@app.get("/api/coas")
def coa_list(token: str = Header(""), db: Session = Depends(get_db)):
    """成品报告列表（放行控制：只有成品合格批才出得了 COA——在库记录即已放行）"""
    u = require_user(token, db)
    require_page(u, "prodlot")
    rows = db.query(M.Coa).order_by(M.Coa.id.desc()).limit(200).all()
    out = []
    for c in rows:
        p = db.get(M.ProductionLot, c.prod_id)
        mat = db.get(M.Material, p.material_id) if p and p.material_id else None
        cust = db.get(M.Customer, c.customer_id) if c.customer_id else None
        out.append({"id": c.id, "coa_no": c.coa_no, "access_key": c.access_key,
                    "prod_id": c.prod_id, "lot_no": p.lot_no if p else "",
                    "material_name": mat.name if mat else "",
                    "customer_name": cust.name if cust else "（通用）",
                    "issued_by": c.issued_by,
                    "created_at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else ""})
    return out


@app.get("/api/coas/{cid}")
def coa_detail(cid: int, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "prodlot")
    c = db.get(M.Coa, cid)
    if not c:
        raise HTTPException(404, "COA 不存在")
    p = db.get(M.ProductionLot, c.prod_id)
    mat = db.get(M.Material, p.material_id) if p and p.material_id else None
    cust = db.get(M.Customer, c.customer_id) if c.customer_id else None
    try:
        items = json.loads(c.items_json or "[]")
    except Exception:
        items = []
    return {"coa_no": c.coa_no, "lot_no": p.lot_no if p else "",
            "material_name": mat.name if mat else "",
            "material_spec": mat.spec if mat else "",
            "customer_name": cust.name if cust else "（通用）",
            "qty": p.qty if p else 0, "unit": p.unit if p else "",
            "issued_by": c.issued_by,
            "created_at": c.created_at.strftime("%Y-%m-%d") if c.created_at else "",
            "items": items}


# 放行控制：成品可用列表（发货环节第5步将只允许引这些批）
@app.get("/api/fg-available")
def fg_available(token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "prodlot")
    rows = db.query(M.ProductionLot).filter(M.ProductionLot.status == 5).all()
    return [_prod_out(db, p) for p in rows]
# ═══════════════════════ 批次追溯（第5步）═══════════════════════
def _trace_test_list(db, prod_id=None, lot_id=None):
    """某批次的全部检验单（含逐项明细与不合格项）"""
    q = db.query(M.TestRecord)
    if prod_id:
        q = q.filter(M.TestRecord.prod_id == prod_id)
    else:
        q = q.filter(M.TestRecord.lot_id == lot_id)
    out = []
    for tr in q.order_by(M.TestRecord.id).all():
        std = db.get(M.QcStandard, tr.std_id)
        items = db.query(M.TestItem).filter(M.TestItem.test_id == tr.id) \
            .order_by(M.TestItem.seq).all()
        out.append({
            "test_no": tr.test_no, "check_type": tr.check_type,
            "check_type_name": COA_CHECK_TYPES.get(tr.check_type, "来料检验"),
            "std_name": std.name if std else "", "result": tr.result,
            "tested_by": tr.tested_by,
            "created_at": tr.created_at.strftime("%Y-%m-%d %H:%M") if tr.created_at else "",
            "items": [{"indicator": it.indicator, "actual": it.actual, "unit": it.unit,
                       "min_val": it.min_val, "max_val": it.max_val,
                       "pass": it.pass_flag, "is_key": it.is_key,
                       "check_by": it.check_by or "dept"} for it in items],
            "fail_items": [it.indicator for it in items if it.pass_flag == 0],
        })
    return out


def _trace_prod_node(db, p, problems):
    st = db.get(M.Station, p.station_id) if p.station_id else None
    eq = db.get(M.Equipment, p.equipment_id) if p.equipment_id else None
    team = db.get(M.Team, p.team_id) if p.team_id else None
    mat = db.get(M.Material, p.material_id) if p.material_id else None
    ncrs = db.query(M.Ncr).filter(M.Ncr.prod_id == p.id).all()
    coa = db.query(M.Coa).filter(M.Coa.prod_id == p.id).order_by(M.Coa.id.desc()).first()
    tests = _trace_test_list(db, prod_id=p.id)
    node = {
        "kind": "production", "lot_no": p.lot_no,
        "station_code": st.code if st else "", "station_name": st.name if st else "",
        "equipment_name": eq.name if eq else "", "team_name": team.name if team else "",
        "operator": p.operator, "qty": p.qty, "unit": p.unit,
        "material_name": f"{mat.name}（{mat.code}）" if mat else "中间料",
        "status": p.status, "status_name": PROD_STATUS.get(p.status, str(p.status)),
        "created_at": p.created_at.strftime("%Y-%m-%d %H:%M") if p.created_at else "",
        "parent_lot_no": p.parent_lot_no, "parent_type": p.parent_type,
        "tests": tests,
        "ncr": [{"ncr_no": n.ncr_no, "status_name": NCR_STATUS.get(n.status, ""),
                 "fail_summary": n.fail_summary, "disposition": n.disposition} for n in ncrs],
        "coa_no": coa.coa_no if coa else None,
    }
    if p.status in (3, 6):
        problems.append({"lot_no": p.lot_no, "station_name": node["station_name"],
                         "reason": "该批次检验不合格被冻结："
                                   + (ncrs[0].fail_summary[:80] if ncrs else "")})
    return node


def _trace_raw_node(db, lot, problems):
    mat = db.get(M.Material, lot.material_id) if lot.material_id else None
    sup = db.get(M.Supplier, lot.supplier_id) if lot.supplier_id else None
    ncrs = db.query(M.Ncr).filter(M.Ncr.lot_id == lot.id).all()
    node = {
        "kind": "incoming", "lot_no": lot.lot_no,
        "material_name": f"{mat.name}（{mat.code}）" if mat else "",
        "supplier_name": sup.name if sup else "", "supplier_lot": lot.supplier_lot,
        "qty": lot.qty, "unit": lot.unit, "vehicle": lot.vehicle,
        "arrival_by": lot.arrival_by,
        "status": lot.status, "status_name": LOT_STATUS.get(lot.status, ""),
        "created_at": lot.created_at.strftime("%Y-%m-%d %H:%M") if lot.created_at else "",
        "tests": _trace_test_list(db, lot_id=lot.id),
        "ncr": [{"ncr_no": n.ncr_no, "status_name": NCR_STATUS.get(n.status, ""),
                 "fail_summary": n.fail_summary, "disposition": n.disposition} for n in ncrs],
        "coa_no": None,
    }
    if lot.status == 3:
        problems.append({"lot_no": lot.lot_no, "station_name": "来料",
                         "reason": "原料检验不合格被冻结：" + (ncrs[0].fail_summary[:80] if ncrs else "")})
    return node


@app.get("/api/trace")
def trace(lot_no: str, token: str = Header(""), db: Session = Depends(get_db)):
    """批次追溯：输入成品/生产/来料批号，向上逐级还原完整链路（含检验数据与不合格标源）"""
    u = require_user(token, db)
    require_page(u, "trace")
    lot_no = (lot_no or "").strip()
    if not lot_no:
        raise HTTPException(400, "请输入批号")
    problems = []
    chain = []
    cur = db.query(M.ProductionLot).filter(M.ProductionLot.lot_no == lot_no).first()
    if cur:
        visited = set()
        while cur and len(chain) < 15:
            chain.append(_trace_prod_node(db, cur, problems))
            if cur.parent_type == "incoming":
                raw = db.query(M.IncomingLot).filter(
                    M.IncomingLot.lot_no == cur.parent_lot_no).first()
                if raw:
                    chain.append(_trace_raw_node(db, raw, problems))
                break
            if not cur.parent_lot_no or cur.lot_no in visited:
                break
            visited.add(cur.lot_no)
            cur = db.query(M.ProductionLot).filter(
                M.ProductionLot.lot_no == cur.parent_lot_no).first()
    else:
        raw = db.query(M.IncomingLot).filter(M.IncomingLot.lot_no == lot_no).first()
        if not raw:
            # 模糊提示相近批号
            hint = db.query(M.ProductionLot).filter(
                M.ProductionLot.lot_no.like(f"%{lot_no}%")).limit(3).all()
            hint2 = db.query(M.IncomingLot).filter(
                M.IncomingLot.lot_no.like(f"%{lot_no}%")).limit(3).all()
            tips = [x.lot_no for x in hint] + [x.lot_no for x in hint2]
            raise HTTPException(404, "未找到该批号" + (f"，相近的有：{tips}" if tips else ""))
        chain.append(_trace_raw_node(db, raw, problems))
    total_tests = sum(len(n["tests"]) for n in chain)
    fail_tests = sum(1 for n in chain for t in n["tests"] if t["result"] == 2)
    return {"lot_no": lot_no, "chain": chain, "problems": problems,
            "summary": {"hops": len(chain), "tests": total_tests,
                        "fail_tests": fail_tests,
                        "has_problem": len(problems) > 0,
                        "coa_no": next((n.get("coa_no") for n in chain if n.get("coa_no")), None)}}


# ═══════════════════════ 统计报表 + Excel 导出（第5步）═══════════════════════
def _rate_of(trs, ctype):
    xs = [t for t in trs if t.check_type == ctype]
    p = len([t for t in xs if t.result == 1])
    f = len([t for t in xs if t.result == 2])
    return {"total": len(xs), "pass": p, "fail": f,
            "rate": round(p * 100.0 / (p + f), 1) if (p + f) else None}


@app.get("/api/reports/summary")
def reports_summary(days: int = 30, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "report")
    days = max(1, min(days, 365))
    since = datetime.now() - timedelta(days=days)
    trs = db.query(M.TestRecord).filter(M.TestRecord.created_at >= since).all()
    ncrs = db.query(M.Ncr).filter(M.Ncr.created_at >= since).all()
    # 柏拉图：不合格项按指标计数
    fails = db.query(M.TestItem).join(
        M.TestRecord, M.TestItem.test_id == M.TestRecord.id).filter(
        M.TestItem.pass_flag == 0, M.TestRecord.created_at >= since).all()
    cnt = {}
    for f in fails:
        cnt[f.indicator] = cnt.get(f.indicator, 0) + 1
    pareto = sorted(({"name": k, "count": v} for k, v in cnt.items()),
                    key=lambda x: -x["count"])
    # 趋势：按日
    trend_map = {}
    for t in trs:
        d = t.created_at.strftime("%m-%d") if t.created_at else "?"
        e = trend_map.setdefault(d, {"date": d, "iqc": [0, 0], "ipqc": [0, 0], "oqc": [0, 0]})
        if t.result in (1, 2) and t.check_type in e:
            e[t.check_type][0] += 1
            if t.result == 1:
                e[t.check_type][1] += 1
    trend = []
    for d in sorted(trend_map.keys()):
        e = trend_map[d]
        row = {"date": d, "iqc": e["iqc"][1], "ipqc": e["ipqc"][1], "oqc": e["oqc"][1]}
        for k in ("iqc", "ipqc", "oqc"):
            tot = e[k][0]
            row[k + "_rate"] = round(e[k][1] * 100.0 / tot, 0) if tot else None
        trend.append(row)
    prod = db.query(M.ProductionLot).all()
    return {
        "days": days,
        "iqc": _rate_of(trs, "iqc"), "ipqc": _rate_of(trs, "ipqc"), "oqc": _rate_of(trs, "oqc"),
        "ncr": {"total": len(ncrs),
                "pending": len([n for n in ncrs if n.status == 0]),
                "by_status": {NCR_STATUS[k]: len([n for n in ncrs if n.status == k])
                              for k in NCR_STATUS}},
        "pareto": pareto[:10],
        "trend": trend,
        "lots": {"production": len(prod),
                 "frozen": len([p for p in prod if p.status in (3, 6)]),
                 "fg_ok": len([p for p in prod if p.status == 5]),
                 "coa": db.query(M.Coa).count()},
    }


@app.get("/api/reports/export")
def reports_export(days: int = 30, token: str = Header(""), db: Session = Depends(get_db)):
    """导出 Excel 月报：汇总 / 检验明细 / 不合格(NCR) 三张表"""
    u = require_user(token, db)
    require_page(u, "report")
    days = max(1, min(days, 365))
    since = datetime.now() - timedelta(days=days)
    import openpyxl
    from openpyxl.styles import Font, Alignment, PatternFill
    wb = openpyxl.Workbook()
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="2F6FA8")

    def style(ws, headers, rows, widths=None):
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill = head_font, head_fill
            c.alignment = Alignment(horizontal="center")
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths or [], start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w

    # Sheet1 汇总
    ws1 = wb.active
    ws1.title = "汇总"
    trs = db.query(M.TestRecord).filter(M.TestRecord.created_at >= since).all()
    ncrs = db.query(M.Ncr).filter(M.Ncr.created_at >= since).all()
    prod = db.query(M.ProductionLot).all()
    style(ws1, ["项目", "数值", "说明"], [
        ["统计区间", f"近 {days} 天", since.strftime("%Y-%m-%d") + " 起"],
        ["来料检验(IQC)", f"{_rate_of(trs,'iqc')['total']} 单", f"合格率 {_rate_of(trs,'iqc')['rate']}%"],
        ["过程检验(IPQC)", f"{_rate_of(trs,'ipqc')['total']} 单", f"合格率 {_rate_of(trs,'ipqc')['rate']}%"],
        ["成品检验(OQC)", f"{_rate_of(trs,'oqc')['total']} 单", f"合格率 {_rate_of(trs,'oqc')['rate']}%"],
        ["NCR 总数", len(ncrs), f"待处理 {len([n for n in ncrs if n.status==0])}"],
        ["生产批次", len(prod), f"冻结 {len([p for p in prod if p.status in (3,6)])} / 成品合格 {len([p for p in prod if p.status==5])}"],
        ["COA 报告", db.query(M.Coa).count(), "成品检验合格自动生成"],
    ], [22, 24, 40])
    # Sheet2 检验明细
    ws2 = wb.create_sheet("检验明细")
    rows2 = []
    for t in sorted(trs, key=lambda x: x.id):
        std = db.get(M.QcStandard, t.std_id)
        obj = ""
        if t.prod_id:
            pl = db.get(M.ProductionLot, t.prod_id)
            obj = pl.lot_no if pl else ""
        elif t.lot_id:
            il = db.get(M.IncomingLot, t.lot_id)
            obj = il.lot_no if il else ""
        for it in db.query(M.TestItem).filter(M.TestItem.test_id == t.id).order_by(M.TestItem.seq).all():
            rows2.append([t.test_no, COA_CHECK_TYPES.get(t.check_type, "来料检验"), obj,
                          std.name if std else "", it.indicator, it.actual, it.unit,
                          (f"≥{it.min_val}" if it.min_val is not None else "") +
                          (f" ≤{it.max_val}" if it.max_val is not None else ""),
                          "合格" if it.pass_flag == 1 else ("不合格" if it.pass_flag == 0 else "未判"),
                          t.tested_by, t.created_at.strftime("%Y-%m-%d %H:%M") if t.created_at else ""])
    style(ws2, ["检验单号", "类型", "批次", "标准", "检验项目", "实测值", "单位", "标准要求", "判定", "检验员", "时间"],
          rows2, [16, 10, 30, 26, 20, 12, 8, 14, 8, 10, 17])
    # Sheet3 NCR
    ws3 = wb.create_sheet("不合格NCR")
    rows3 = []
    for n in sorted(ncrs, key=lambda x: x.id):
        no, mat = "", ""
        if n.prod_id:
            pl = db.get(M.ProductionLot, n.prod_id)
            no = pl.lot_no if pl else ""
            m = db.get(M.Material, pl.material_id) if pl and pl.material_id else None
            mat = m.name if m else "中间料"
        elif n.lot_id:
            il = db.get(M.IncomingLot, n.lot_id)
            no = il.lot_no if il else ""
            m = db.get(M.Material, il.material_id) if il and il.material_id else None
            mat = m.name if m else ""
        rows3.append([n.ncr_no, no, mat, n.fail_summary, NCR_STATUS.get(n.status, ""),
                      n.disposition, n.created_by,
                      n.created_at.strftime("%Y-%m-%d %H:%M") if n.created_at else "", n.remark])
    style(ws3, ["NCR号", "批次", "物料", "不合格描述", "状态", "处置", "发起", "时间", "备注"],
          rows3, [14, 30, 16, 46, 12, 10, 10, 17, 24])
    buf = io.BytesIO()
    wb.save(buf)
    fname = urllib.parse.quote(f"质量月报_{datetime.now().strftime('%Y%m%d')}.xlsx")
    return Response(content=buf.getvalue(),
                    media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fname}"})


# ═══════════════════════ SPC 控制图（第5步）═══════════════════════
def _numeric_series(db, check_type, indicator, object_type=None, object_id=None):
    """取某检验类型+指标(+工序/物料维度)的历史实测值序列（时间正序，仅数值）"""
    q = db.query(M.TestItem, M.TestRecord).join(
        M.TestRecord, M.TestItem.test_id == M.TestRecord.id).filter(
        M.TestRecord.check_type == check_type, M.TestItem.indicator == indicator)
    if object_type == "station" and object_id:
        q = q.join(M.ProductionLot, M.TestRecord.prod_id == M.ProductionLot.id) \
             .filter(M.ProductionLot.station_id == object_id)
    elif object_type == "material" and object_id:
        q = q.join(M.IncomingLot, M.TestRecord.lot_id == M.IncomingLot.id) \
             .filter(M.IncomingLot.material_id == object_id)
    rows = q.order_by(M.TestRecord.id).all()
    out = []
    for it, tr in rows:
        try:
            v = float(str(it.actual).strip())
        except (TypeError, ValueError):
            continue
        label = ""
        if tr.prod_id:
            pl = db.get(M.ProductionLot, tr.prod_id)
            label = (pl.lot_no[-28:] if pl else str(tr.id))
        else:
            il = db.get(M.IncomingLot, tr.lot_id)
            label = (il.lot_no if il else str(tr.id))
        out.append({"value": v, "label": label, "test_no": tr.test_no,
                    "lot_id": tr.lot_id, "prod_id": tr.prod_id,
                    "min_val": it.min_val, "max_val": it.max_val,
                    "date": tr.created_at.strftime("%m-%d") if tr.created_at else ""})
    return out


@app.get("/api/spc/items")
def spc_items(token: str = Header(""), db: Session = Depends(get_db)):
    """可做控制图的指标清单（数值型且样本≥6）"""
    u = require_user(token, db)
    require_page(u, "report")
    sts = {st.id: st for st in db.query(M.Station).all()}
    mats = {m.id: m for m in db.query(M.Material).all()}
    prods = {p.id: p for p in db.query(M.ProductionLot).all()}
    lots = {l.id: l for l in db.query(M.IncomingLot).all()}
    rows = db.query(M.TestItem, M.TestRecord).join(
        M.TestRecord, M.TestItem.test_id == M.TestRecord.id).all()
    agg = {}
    for it, tr in rows:
        try:
            float(str(it.actual).strip())
        except (TypeError, ValueError):
            continue
        # 按 检验类型 + 指标 + 对象(工序/物料) 三维聚合，避免混工况
        otype, oid, scope = None, None, "-"
        if tr.prod_id and tr.prod_id in prods:
            pl = prods[tr.prod_id]
            otype, oid = "station", pl.station_id
            scope = f"工序:{sts[pl.station_id].name}" if pl.station_id in sts else "-"
        elif tr.lot_id and tr.lot_id in lots:
            il = lots[tr.lot_id]
            otype, oid = "material", il.material_id
            scope = f"物料:{mats[il.material_id].name}" if il.material_id in mats else "-"
        key = (tr.check_type, it.indicator, otype, oid)
        agg.setdefault(key, []).append(tr)
    out = []
    for (ct, ind, otype, oid), trs in agg.items():
        if len(trs) < 6:
            continue
        scopes = set()
        for tr in trs:
            if tr.prod_id and tr.prod_id in prods:
                pl = prods[tr.prod_id]
                if pl.station_id in sts:
                    scopes.add(f"工序:{sts[pl.station_id].name}")
            elif tr.lot_id and tr.lot_id in lots:
                il = lots[tr.lot_id]
                if il.material_id in mats:
                    scopes.add(f"物料:{mats[il.material_id].name}")
        out.append({"check_type": ct, "check_type_name": COA_CHECK_TYPES.get(ct, "来料检验"),
                    "indicator": ind, "n": len(trs), "object_type": otype,
                    "object_id": oid, "scope": "、".join(sorted(scopes)) or "-"})
    out.sort(key=lambda x: -x["n"])
    return out


@app.get("/api/spc/chart")
def spc_chart(check_type: str, indicator: str, object_type: str = "", object_id: int = 0,
              limit: int = 25, token: str = Header(""), db: Session = Depends(get_db)):
    """I-MR 控制图：单值(X) + 移动极差(MR)，UCL/LCL=X̄±2.66·MR̄；越界点自动预警"""
    u = require_user(token, db)
    require_page(u, "report")
    series = _numeric_series(db, check_type, indicator,
                             object_type or None, object_id or None)
    if len(series) < 6:
        raise HTTPException(400, f"样本不足（{len(series)} 个数值点，至少需要 6 个）")
    series = series[-limit:]
    vals = [s["value"] for s in series]
    xbar = sum(vals) / len(vals)
    mrs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
    mrbar = sum(mrs) / len(mrs) if mrs else 0
    # 退化保护：所有值相同时 MR̄=0，控制限会塌缩成一条线导致全部误报。
    # 此时改用样本标准差(±3σ)；若标准差也为 0（数据真的无波动）则明确提示不评估。
    note = ""
    if mrbar <= 1e-9:
        var = sum((v - xbar) ** 2 for v in vals) / max(len(vals) - 1, 1)
        sd = var ** 0.5
        if sd <= 1e-9:
            return {"check_type": check_type, "indicator": indicator,
                    "center": round(xbar, 4), "ucl": None, "lcl": None,
                    "mr_center": 0, "ucl_mr": None, "sigma_est": 0,
                    "points": [{"idx": i, "value": v, "label": series[i]["label"],
                                "date": series[i]["date"], "oc": False}
                               for i, v in enumerate(vals)],
                    "out_of_control": [], "spec": {"min_val": series[-1].get("min_val"),
                                                   "max_val": series[-1].get("max_val")},
                    "alarm": False,
                    "note": "该指标历史实测值完全一致（无波动），暂无法评估控制限；请补充多样本后再看控制图"}
        mrbar_eff = sd * 1.128
    else:
        mrbar_eff = mrbar
    ucl = xbar + 2.66 * mrbar_eff
    lcl = xbar - 2.66 * mrbar_eff
    out_of_control = []
    points = []
    for i, s in enumerate(series):
        oc = (s["value"] > ucl) or (s["value"] < lcl)
        if oc:
            out_of_control.append({"label": s["label"], "value": s["value"], "date": s["date"],
                                   "side": "超上限" if s["value"] > ucl else "超下限"})
        points.append({"idx": i, "value": s["value"], "label": s["label"],
                       "date": s["date"], "diff": i == 0 or abs(s["value"] - series[i-1]["value"]) > 0,
                       "oc": oc})
    spec = {"min_val": series[-1].get("min_val"), "max_val": series[-1].get("max_val")}
    return {"check_type": check_type, "indicator": indicator, "note": note,
            "center": round(xbar, 3), "ucl": round(ucl, 3), "lcl": round(lcl, 3),
            "mr_center": round(mrbar, 3), "ucl_mr": round(3.267 * mrbar, 3),
            "sigma_est": round(mrbar_eff / 1.128, 3),
            "points": points, "out_of_control": out_of_control, "spec": spec,
            "alarm": len(out_of_control) > 0}


# ═══════════════════════ 车间大屏（第5步）═══════════════════════
@app.get("/api/screen")
def screen_data(token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "screen")
    now = datetime.now()
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_trs = db.query(M.TestRecord).filter(M.TestRecord.created_at >= today0).all()
    today_pass = len([t for t in today_trs if t.result == 1])
    today_fail = len([t for t in today_trs if t.result == 2])
    since = now - timedelta(days=30)
    trs30 = db.query(M.TestRecord).filter(M.TestRecord.created_at >= since).all()
    sts = {st.id: st for st in db.query(M.Station).all()}
    # 各工序合格率（近30天 ipqc）
    line = {}
    for t in trs30:
        if t.check_type != "ipqc" or t.result not in (1, 2) or not t.prod_id:
            continue
        pl = db.get(M.ProductionLot, t.prod_id)
        if not pl:
            continue
        nm = sts[pl.station_id].name if pl.station_id in sts else "?"
        e = line.setdefault(nm, [0, 0])
        e[0] += 1
        if t.result == 1:
            e[1] += 1
    lines = [{"station": k, "tests": v[0],
              "rate": round(v[1] * 100.0 / v[0], 0) if v[0] else None}
             for k, v in sorted(line.items(), key=lambda x: -x[1][0])]
    # 待办
    todo = {
        "iqc_wait": db.query(M.IncomingLot).filter(M.IncomingLot.status.in_([0, 1])).count(),
        "prod_wait": db.query(M.ProductionLot).filter(M.ProductionLot.status == 1).count(),
        "oqc_wait": db.query(M.ProductionLot).filter(M.ProductionLot.status == 4).count(),
        "ncr_open": db.query(M.Ncr).filter(M.Ncr.status == 0).count(),
        "frozen": db.query(M.ProductionLot).filter(M.ProductionLot.status.in_([3, 6])).count(),
    }
    # 最近事件
    events = []
    for t in db.query(M.TestRecord).order_by(M.TestRecord.id.desc()).limit(12).all():
        obj, mat = "", ""
        if t.prod_id:
            pl = db.get(M.ProductionLot, t.prod_id)
            obj = pl.lot_no if pl else ""
            m = db.get(M.Material, pl.material_id) if pl and pl.material_id else None
            mat = m.name if m else ""
        elif t.lot_id:
            il = db.get(M.IncomingLot, t.lot_id)
            obj = il.lot_no if il else ""
            m = db.get(M.Material, il.material_id) if il and il.material_id else None
            mat = m.name if m else ""
        events.append({"time": t.created_at.strftime("%m-%d %H:%M") if t.created_at else "",
                       "test_no": t.test_no,
                       "type": COA_CHECK_TYPES.get(t.check_type, "来料检验"),
                       "lot_no": obj, "material": mat, "result": t.result,
                       "by": t.tested_by})
    return {"updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "kpi": {"today_tests": len(today_trs), "today_pass": today_pass,
                    "today_fail": today_fail,
                    "today_rate": round(today_pass * 100.0 / (today_pass + today_fail), 0)
                    if (today_pass + today_fail) else None,
                    "fg_ok": db.query(M.ProductionLot).filter(M.ProductionLot.status == 5).count(),
                    "coa": db.query(M.Coa).count()},
            "todo": todo, "lines": lines, "events": events,
            "rates": {"iqc": _rate_of(trs30, "iqc")["rate"],
                      "ipqc": _rate_of(trs30, "ipqc")["rate"],
                      "oqc": _rate_of(trs30, "oqc")["rate"]}}


# ── 建批：本工序可选的父批（首工序列合格原料批；其余列上工序合格生产批）──
@app.get("/api/production-lots/available-parents")
def available_parents(station_id: int, token: str = Header(""),
                      db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "prodlot")
    prevs = _prev_stations(db, station_id)
    out = []
    if not prevs:
        for lot in db.query(M.IncomingLot).filter(M.IncomingLot.status.in_([2, 4])).all():
            mat = db.get(M.Material, lot.material_id) if lot.material_id else None
            out.append({"type": "incoming", "lot_no": lot.lot_no,
                        "label": f"[原料] {lot.lot_no} · {mat.name if mat else ''} · "
                                 f"{lot.qty}{lot.unit} · {LOT_STATUS.get(lot.status)}",
                        "status_name": LOT_STATUS.get(lot.status)})
    else:
        for pl in db.query(M.ProductionLot).filter(
                M.ProductionLot.station_id.in_(prevs),
                M.ProductionLot.status.in_([2, 5])).all():
            st = db.get(M.Station, pl.station_id)
            mat = db.get(M.Material, pl.material_id) if pl.material_id else None
            out.append({"type": "production", "lot_no": pl.lot_no,
                        "label": f"[上工序·{st.name if st else ''}] {pl.lot_no} · "
                                 f"{mat.name if mat else '中间料'} · {pl.qty}{pl.unit} · "
                                 f"{PROD_STATUS.get(pl.status)}",
                        "status_name": PROD_STATUS.get(pl.status)})
    return out


# ── COA 公开页（客户扫码查看，无需登录；只含对外信息）──
def _coa_public_html(db, c) -> str:
    p = db.get(M.ProductionLot, c.prod_id)
    mat = db.get(M.Material, p.material_id) if p and p.material_id else None
    cust = db.get(M.Customer, c.customer_id) if c.customer_id else None
    try:
        items = json.loads(c.items_json or "[]")
    except Exception:
        items = []
    rows = ""
    for it in items:
        lim = ""
        if it.get("min_val") is not None:
            lim += f"≥{it['min_val']}"
        if it.get("max_val") is not None:
            lim += ("/" if lim else "") + f"≤{it['max_val']}"
        ok = it.get("pass") == 1
        rows += (f"<tr><td>{it.get('indicator','')}</td><td>{lim or '—'}</td>"
                 f"<td><b>{it.get('actual','')}</b> {it.get('unit','')}</td>"
                 f"<td class=\"{'ok' if ok else 'bad'}\">{'合格' if ok else '不合格'}</td></tr>")
    issued = c.created_at.strftime("%Y-%m-%d") if c.created_at else ""
    return f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>质量检验报告 {c.coa_no}</title>
<style>
body{{font-family:"Microsoft YaHei",system-ui,sans-serif;background:#f4f7fb;margin:0;padding:18px;color:#22303f}}
.card{{max-width:720px;margin:0 auto;background:#fff;border:1px solid #dfe8f3;border-radius:12px;padding:22px 24px}}
h1{{font-size:19px;margin:0 0 4px}} .sub{{color:#6b7c93;font-size:12.5px;margin-bottom:14px}}
table{{width:100%;border-collapse:collapse;font-size:13px;margin-top:10px}}
th,td{{border:1px solid #e3ecf6;padding:7px 9px;text-align:left}}
th{{background:#f2f7fd;font-weight:600;color:#3d5a7a}}
.ok{{color:#18864b;font-weight:600}} .bad{{color:#c92a3d;font-weight:600}}
.meta{{display:grid;grid-template-columns:1fr 1fr;gap:6px 18px;font-size:13px;margin-top:6px}}
.meta b{{color:#3d5a7a}}
.qr{{text-align:center;margin-top:16px}} .qr img{{width:130px;height:130px}}
.foot{{margin-top:14px;font-size:11.5px;color:#8595a8;text-align:center}}
.badge{{display:inline-block;background:#e8f7ee;color:#18864b;border:1px solid #bfe6cd;border-radius:20px;padding:2px 12px;font-size:12px;font-weight:600}}
</style></head><body><div class="card">
<h1>成品质量检验报告（COA）</h1>
<div class="sub">报告编号 <b>{c.coa_no}</b> ｜ 签发日期 {issued} <span class="badge">检验合格</span></div>
<div class="meta">
<div><b>产品名称：</b>{mat.name if mat else '—'}</div>
<div><b>产品规格：</b>{mat.spec if mat else '—'}</div>
<div><b>生产批号：</b>{p.lot_no if p else '—'}</div>
<div><b>数量：</b>{p.qty if p else 0} {p.unit if p else ''}</div>
<div><b>客户：</b>{cust.name if cust else '（通用）'}</div>
<div><b>签发：</b>{c.issued_by or ''}</div>
</div>
<table><thead><tr><th>检验项目</th><th>标准要求</th><th>实测结果</th><th>判定</th></tr></thead>
<tbody>{rows}</tbody></table>
<div class="qr"><img src="/api/public/coa/{c.coa_no}/qr.svg?k={c.access_key}" alt="扫码查看"><div style="font-size:11px;color:#8595a8;margin-top:4px">扫码查看本报告</div></div>
<div class="foot">本报告由生产质量管理系统自动生成，数据来源于成品检验（OQC）原始记录</div>
</div></body></html>"""


@app.get("/coa/{coa_no}", response_class=HTMLResponse)
def coa_public(coa_no: str, k: str = "", db: Session = Depends(get_db)):
    """客户扫码公开展示页（无需登录，但需要链接里的随机口令 k）
    方案A：只把带口令的链接发给对应客户，防止链接被随意转发/枚举查看"""
    c = db.query(M.Coa).filter(M.Coa.coa_no == coa_no).first()
    if not c:
        return HTMLResponse(_coa_err_html("报告不存在或已作废"), status_code=404)
    if not c.access_key or c.access_key != (k or "").strip():
        return HTMLResponse(_coa_err_html(
            "链接无效或已更新<br><span style=\"font-size:13px;color:#6b7c93\">"
            "请向业务员/质量部索取最新链接（旧链接在重置后会失效）</span>"), status_code=403)
    return HTMLResponse(_coa_public_html(db, c))


def _coa_err_html(msg):
    return ("<html><head><meta charset='utf-8'><title>无法查看报告</title></head>"
            "<body style=\"font-family:-apple-system,'Microsoft YaHei',sans-serif;"
            "display:flex;align-items:center;justify-content:center;height:100vh;margin:0;background:#f4f8fc\">"
            "<div style=\"text-align:center;color:#2b3b52;background:#fff;border:1px solid #e3ecf6;"
            "border-radius:12px;padding:28px 34px\"><div style=\"font-size:34px\">🔒</div>"
            f"<h3 style=\"margin:10px 0\">{msg}</h3></div></body></html>")


@app.get("/api/public/coa/{coa_no}/qr.svg")
def coa_qr(coa_no: str, k: str = "", request: Request = None, db: Session = Depends(get_db)):
    """COA 二维码：内容=带口令的完整地址（手机扫码直接打开并校验口令）"""
    import qrcode
    import qrcode.image.svg
    c = db.query(M.Coa).filter(M.Coa.coa_no == coa_no).first()
    if not c or not c.access_key or c.access_key != (k or "").strip():
        raise HTTPException(403, "链接口令无效")
    base = str(request.base_url).rstrip("/") if request is not None else ""
    url = f"{base}/coa/{coa_no}?k={c.access_key}"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    buf = io.BytesIO()
    img.save(buf)
    return Response(content=buf.getvalue(), media_type="image/svg+xml",
                    headers={"X-QMS-URL": url})


def _coa_link(request, c):
    base = str(request.base_url).rstrip("/") if request is not None else ""
    return f"{base}/coa/{c.coa_no}?k={c.access_key}"


@app.get("/api/coas/{cid}/link")
def coa_link(cid: int, request: Request = None, token: str = Header(""),
             db: Session = Depends(get_db)):
    """内部：取某张 COA 的客户链接（发客户用）"""
    u = require_user(token, db)
    require_page(u, "prodlot")
    c = db.get(M.Coa, cid)
    if not c:
        raise HTTPException(404, "报告不存在")
    if not c.access_key:
        c.access_key = secrets.token_hex(16)
        db.commit()
    return {"ok": True, "coa_no": c.coa_no, "key": c.access_key,
            "url": _coa_link(request, c)}


@app.post("/api/coas/{cid}/rotate-key")
def coa_rotate_key(cid: int, request: Request = None, token: str = Header(""),
                   db: Session = Depends(get_db)):
    """重置客户链接口令（质量经理/管理员）：旧链接立即失效，返回新链接"""
    u = require_user(token, db)
    require_page(u, "prodlot")
    _require_action(u, ["admin", "qm"])
    c = db.get(M.Coa, cid)
    if not c:
        raise HTTPException(404, "报告不存在")
    c.access_key = secrets.token_hex(16)
    db.commit()
    audit(db, u.username, "update", f"coa:{c.coa_no}", "重置客户链接口令（旧链接失效）")
    db.commit()
    return {"ok": True, "coa_no": c.coa_no, "key": c.access_key, "url": _coa_link(request, c)}

# ═══════════════════════ 数据大屏（管理层，第6步增强）═══════════════════════
@app.get("/api/board")
def board_data(days: int = 30, token: str = Header(""), db: Session = Depends(get_db)):
    """管理层汇总：全厂 KPI / 供应商来料合格率排名 / 工序合格率 / NCR 趋势与原因 /
    SPC 失控预警汇总 / 成品交付"""
    u = require_user(token, db)
    require_page(u, "board")
    now = datetime.now()
    days = max(7, min(int(days), 365))
    since = now - timedelta(days=days)
    month0 = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    trs = db.query(M.TestRecord).filter(M.TestRecord.created_at >= since).all()
    trs_m = [t for t in trs if t.created_at and t.created_at >= month0]

    def rate(xs):
        p = len([t for t in xs if t.result == 1])
        f = len([t for t in xs if t.result == 2])
        return {"total": len(xs), "pass": p, "fail": f,
                "rate": round(p * 100.0 / (p + f), 1) if (p + f) else None}

    # 供应商来料合格率排名（管理层最关心：哪家供应商质量好/差）
    sups = {sp.id: sp for sp in db.query(M.Supplier).all()}
    lots = db.query(M.IncomingLot).filter(M.IncomingLot.created_at >= since).all()
    agg = {}
    for lot in lots:
        if lot.status not in (2, 3, 4, 5, 6):      # 只统计已判定的
            continue
        e = agg.setdefault(lot.supplier_id, [0, 0])
        e[0] += 1
        if lot.status in (2, 4):
            e[1] += 1
    supplier_rates = sorted(
        ({"supplier": sups[k].name if k in sups else "?",
          "lots": v[0], "ok": v[1],
          "rate": round(v[1] * 100.0 / v[0], 1) if v[0] else None}
         for k, v in agg.items()),
        key=lambda x: (x["rate"] is None, x["rate"]))
    # 各工序过程合格率
    sts = {st.id: st for st in db.query(M.Station).all()}
    prods = {pl.id: pl for pl in db.query(M.ProductionLot).all()}
    st_agg = {}
    for t in trs:
        if t.check_type != "ipqc" or t.result not in (1, 2) or not t.prod_id:
            continue
        pl = prods.get(t.prod_id)
        if not pl:
            continue
        e = st_agg.setdefault(pl.station_id, [0, 0])
        e[0] += 1
        if t.result == 1:
            e[1] += 1
    station_rates = [{"station": sts[k].name if k in sts else "?",
                      "tests": v[0], "rate": round(v[1] * 100.0 / v[0], 0) if v[0] else None}
                     for k, v in sorted(st_agg.items(), key=lambda x: -x[1][0])]
    # NCR：趋势 + 原因 TOP
    ncrs = db.query(M.Ncr).filter(M.Ncr.created_at >= since).all()
    trend_map = {}
    for n in ncrs:
        if not n.created_at:
            continue
        d = n.created_at.strftime("%m-%d")
        trend_map[d] = trend_map.get(d, 0) + 1
    ncr_trend = [{"date": d, "count": trend_map[d]} for d in sorted(trend_map)]
    fails = db.query(M.TestItem).join(
        M.TestRecord, M.TestItem.test_id == M.TestRecord.id).filter(
        M.TestItem.pass_flag == 0, M.TestRecord.created_at >= since).all()
    cnt = {}
    for f in fails:
        cnt[f.indicator] = cnt.get(f.indicator, 0) + 1
    fail_pareto = sorted(({"name": k, "count": v} for k, v in cnt.items()),
                         key=lambda x: -x["count"])[:8]
    # SPC 失控预警汇总
    alarm_items = []
    try:
        sts_map = {st.id: st for st in db.query(M.Station).all()}
        mats_map = {m.id: m for m in db.query(M.Material).all()}
        prods_map = {pl.id: pl for pl in db.query(M.ProductionLot).all()}
        lots_map = {l.id: l for l in db.query(M.IncomingLot).all()}
        rows = db.query(M.TestItem, M.TestRecord).join(
            M.TestRecord, M.TestItem.test_id == M.TestRecord.id).all()
        agg2 = {}
        for it, tr in rows:
            try:
                float(str(it.actual).strip())
            except (TypeError, ValueError):
                continue
            otype = oid = None
            scope = "-"
            if tr.prod_id and tr.prod_id in prods_map:
                pl = prods_map[tr.prod_id]
                otype, oid = "station", pl.station_id
                scope = sts_map[pl.station_id].name if pl.station_id in sts_map else "-"
            elif tr.lot_id and tr.lot_id in lots_map:
                il = lots_map[tr.lot_id]
                otype, oid = "material", il.material_id
                scope = mats_map[il.material_id].name if il.material_id in mats_map else "-"
            agg2.setdefault((tr.check_type, it.indicator, otype, oid, scope), []).append(tr)
        for (ct, ind, otype, oid, scope), ts in agg2.items():
            if len(ts) < 6 or not otype:
                continue
            series = _numeric_series(db, ct, ind, otype, oid)
            vals = [x["value"] for x in series][-25:]
            if len(vals) < 6:
                continue
            xbar = sum(vals) / len(vals)
            mrs = [abs(vals[i] - vals[i - 1]) for i in range(1, len(vals))]
            mrbar = sum(mrs) / len(mrs) if mrs else 0
            if mrbar <= 1e-9:
                continue
            ucl, lcl = xbar + 2.66 * mrbar, xbar - 2.66 * mrbar
            oc = [v for v in vals if v > ucl or v < lcl]
            if oc:
                alarm_items.append({"scope": scope, "indicator": ind,
                                    "check_type": COA_CHECK_TYPES.get(ct, ct),
                                    "out": len(oc), "latest": oc[-1],
                                    "side": "超上限" if oc[-1] > ucl else "超下限"})
    except Exception:      # noqa: BLE001
        pass
    # 成品交付
    coas = db.query(M.Coa).filter(M.Coa.created_at >= since).all()
    pls = db.query(M.ProductionLot).all()
    return {
        "updated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "days": days,
        "kpi": {
            "month_tests": len(trs_m),
            "month_rate": rate(trs_m)["rate"],
            "range_rate": rate(trs)["rate"],
            "iqc": rate([t for t in trs if t.check_type == "iqc"]),
            "ipqc": rate([t for t in trs if t.check_type == "ipqc"]),
            "oqc": rate([t for t in trs if t.check_type == "oqc"]),
            "ncr": len(ncrs),
            "ncr_open": len([n for n in ncrs if n.status == 0]),
            "ncr_close_rate": round(len([n for n in ncrs if n.status != 0]) * 100.0 / len(ncrs), 0)
            if ncrs else None,
            "coa": len(coas),
            "fg_ok": len([p for p in pls if p.status == 5]),
            "frozen": len([p for p in pls if p.status in (3, 6)]),
        },
        "supplier_rates": supplier_rates,
        "station_rates": station_rates,
        "ncr_trend": ncr_trend,
        "fail_pareto": fail_pareto,
        "spc_alarm": {"count": len(alarm_items), "items": alarm_items[:6]},
        "complaint": {
            "total": db.query(M.Complaint).filter(M.Complaint.created_at >= since).count(),
            "open": db.query(M.Complaint).filter(
                M.Complaint.status.in_([0, 1]), M.Complaint.created_at >= since).count(),
            "closing": db.query(M.Complaint).filter(
                M.Complaint.status == 3, M.Complaint.created_at >= since).count(),
        },
    }


@app.get("/api/lan-qr")
def lan_qr(token: str = Header(""), db: Session = Depends(get_db)):
    """手机扫码访问：生成"本机局域网地址"的二维码（车间贴一张，手机扫码即用）"""
    require_user(token, db)
    import socket
    import qrcode
    import qrcode.image.svg
    ip = "127.0.0.1"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
    except Exception:      # noqa: BLE001
        pass
    url = f"http://{ip}:8000"
    img = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=1)
    buf = io.BytesIO()
    img.save(buf)
    from fastapi.responses import Response as _R
    return _R(content=buf.getvalue(), media_type="image/svg+xml",
              headers={"X-QMS-URL": url})


# ═══════════════════════ 岗位职责（部门+职务 → 模块权限）═══════════════════════
@app.get("/api/duty-matrix")
def duty_matrix(token: str = Header(""), db: Session = Depends(get_db)):
    """岗位职责配置页所需数据：部门、职务、职责模板、可选模块清单"""
    u = require_user(token, db)
    require_page(u, "duty")
    deps = db.query(M.Department).filter(M.Department.enabled == True).order_by(M.Department.seq, M.Department.id).all()
    poss = db.query(M.Position).filter(M.Position.enabled == True).order_by(M.Position.seq, M.Position.id).all()
    tpls = db.query(M.DutyTemplate).filter(M.DutyTemplate.enabled == True).all()
    return {
        "departments": [{"id": d.id, "code": d.code, "name": d.name} for d in deps],
        "positions": [{"id": p.id, "code": p.code, "name": p.name} for p in poss],
        "templates": [{"id": t.id, "dept_id": t.dept_id, "position_id": t.position_id,
                       "view_pages": _load_json_list(t.view_pages) or [],
                       "manage_modules": _load_json_list(t.manage_modules) or [],
                       "updated_by": t.updated_by or "",
                       "updated_at": t.updated_at.strftime("%Y-%m-%d %H:%M") if t.updated_at else ""}
                      for t in tpls],
        "modules": [{"key": k, "name": v[0], "group": v[1]} for k, v in PAGES.items()],
        "roles": [{"key": k, "name": ROLE_NAMES.get(k, k), "pages": ROLE_PAGES.get(k, []),
                   "manage": ROLE_MANAGE.get(k, [])} for k in ROLE_PAGES],
        "user_count": {t.position_id: db.query(M.User).filter(
            M.User.position_id == t.position_id, M.User.enabled == True).count()
            for t in tpls},
    }


class DutyIn(BaseModel):
    dept_id: int | None = None        # 空 = 通用职务（任何部门兜底）
    position_id: int
    view_pages: list = []
    manage_modules: list = []
    remark: str = ""


@app.post("/api/duty-templates")
def duty_save(body: DutyIn, token: str = Header(""), db: Session = Depends(get_db)):
    """新增/更新一个"部门+职务"的职责模板（同组合覆盖）"""
    u = require_user(token, db)
    require_page(u, "duty"); require_manage(u, "duty")
    if not db.get(M.Position, body.position_id):
        raise HTTPException(400, "职务不存在")
    if body.dept_id and not db.get(M.Department, body.dept_id):
        raise HTTPException(400, "部门不存在")
    pages = [k for k in body.view_pages if k in PAGES]
    mgs = [k for k in body.manage_modules if k in PAGES]
    if not pages:
        raise HTTPException(400, "至少要勾选一个可见模块")
    bad = [k for k in mgs if k not in pages]
    if bad:
        raise HTTPException(400, f"可管理模块必须同时可见：{'、'.join(PAGES[k][0] for k in bad)}")
    q = db.query(M.DutyTemplate).filter(M.DutyTemplate.position_id == body.position_id,
                                        M.DutyTemplate.enabled == True)
    q = q.filter(M.DutyTemplate.dept_id == body.dept_id) if body.dept_id else q.filter(M.DutyTemplate.dept_id.is_(None))
    t = q.first()
    if t:
        t.view_pages = json.dumps(pages, ensure_ascii=False)
        t.manage_modules = json.dumps(mgs, ensure_ascii=False)
        t.remark = body.remark or t.remark
        t.updated_by = u.username
        t.updated_at = datetime.now()
        action = "update"
    else:
        t = M.DutyTemplate(dept_id=body.dept_id, position_id=body.position_id,
                           view_pages=json.dumps(pages, ensure_ascii=False),
                           manage_modules=json.dumps(mgs, ensure_ascii=False),
                           remark=body.remark, updated_by=u.username)
        db.add(t)
        action = "create"
    db.commit()
    audit(db, u.username, action, f"duty:{body.position_id}",
          f"部门{body.dept_id or '通用'} 模块{len(pages)}个")
    db.commit()
    return {"ok": True, "id": t.id}


@app.delete("/api/duty-templates/{tid}")
def duty_delete(tid: int, token: str = Header(""), db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "duty"); require_manage(u, "duty")
    t = db.get(M.DutyTemplate, tid)
    if not t:
        raise HTTPException(404, "职责模板不存在")
    t.enabled = False
    t.updated_by = u.username
    t.updated_at = datetime.now()
    db.commit()
    audit(db, u.username, "delete", f"duty:{tid}", "停用职责模板")
    db.commit()
    return {"ok": True}


# ═══════════════════════ 客诉管理（第三批）═══════════════════════
COMPLAINT_STATUS = {0: "待受理", 1: "调查中", 2: "已回复", 3: "已关闭"}
CLAIM_TYPES = ["质量异议", "包装标识", "交期", "其他"]
SEVERITY_NAMES = {1: "一般", 2: "严重", 3: "重大"}


def _complaint_out(db, c):
    cu = db.get(M.Customer, c.customer_id) if c.customer_id else None
    return {"id": c.id, "complaint_no": c.complaint_no,
            "customer_id": c.customer_id, "customer_name": cu.name if cu else "",
            "lot_no": c.lot_no, "coa_no": c.coa_no,
            "claim_type": c.claim_type, "severity": c.severity,
            "severity_name": SEVERITY_NAMES.get(c.severity, "?"),
            "title": c.title, "content": c.content,
            "root_cause": c.root_cause, "action": c.action, "reply": c.reply,
            "status": c.status, "status_name": COMPLAINT_STATUS.get(c.status, "?"),
            "created_by": c.created_by,
            "handled_by": c.handled_by,
            "handled_at": c.handled_at.strftime("%Y-%m-%d %H:%M") if c.handled_at else "",
            "closed_by": c.closed_by,
            "closed_at": c.closed_at.strftime("%Y-%m-%d %H:%M") if c.closed_at else "",
            "created_at": c.created_at.strftime("%Y-%m-%d %H:%M") if c.created_at else "",
            "updated_at": c.updated_at.strftime("%Y-%m-%d %H:%M") if c.updated_at else ""}


@app.get("/api/complaints")
def complaint_list(status: str = "", keyword: str = "", token: str = Header(""),
                   db: Session = Depends(get_db)):
    u = require_user(token, db)
    require_page(u, "complaint")
    q = db.query(M.Complaint)
    if status != "" and status is not None:
        q = q.filter(M.Complaint.status == int(status))
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(M.Complaint.complaint_no.like(like), M.Complaint.title.like(like),
                         M.Complaint.lot_no.like(like), M.Complaint.content.like(like)))
    rows = q.order_by(M.Complaint.id.desc()).limit(200).all()
    out = [_complaint_out(db, c) for c in rows]
    stat = {k: len([c for c in rows if c.status == k]) for k in COMPLAINT_STATUS}
    return {"rows": out, "stat": stat, "total": len(rows),
            "open": len([c for c in rows if c.status in (0, 1)])}


class ComplaintIn(BaseModel):
    customer_id: int | None = None
    lot_no: str = ""
    coa_no: str = ""
    claim_type: str = "质量异议"
    severity: int = 1
    title: str
    content: str = ""


@app.post("/api/complaints")
def complaint_create(body: ComplaintIn, token: str = Header(""), db: Session = Depends(get_db)):
    """客诉登记：质量部/采购/管理员都可登记"""
    u = require_user(token, db)
    require_page(u, "complaint")
    _require_action(u, ["admin", "qm", "qc", "buyer"])
    if not body.title.strip():
        raise HTTPException(400, "请填写投诉主题")
    if body.customer_id and not db.get(M.Customer, body.customer_id):
        raise HTTPException(400, "客户不存在")
    if body.severity not in (1, 2, 3):
        raise HTTPException(400, "严重程度只能是 1一般/2严重/3重大")
    if body.claim_type not in CLAIM_TYPES:
        raise HTTPException(400, "投诉类型不合法")
    no = _next_no(db, M.Complaint, "complaint_no", f"CS-{datetime.now().year}-")
    c = M.Complaint(complaint_no=no, customer_id=body.customer_id,
                    lot_no=body.lot_no.strip(), coa_no=body.coa_no.strip(),
                    claim_type=body.claim_type, severity=body.severity,
                    title=body.title.strip(), content=body.content.strip(),
                    status=0, created_by=u.username)
    db.add(c)
    db.commit()
    audit(db, u.username, "create", f"complaint:{no}", f"客诉登记：{body.title[:40]}")
    db.commit()
    return {"ok": True, "id": c.id, "complaint_no": no}


class ComplaintEditIn(BaseModel):
    claim_type: str | None = None
    severity: int | None = None
    title: str | None = None
    content: str | None = None
    lot_no: str | None = None
    coa_no: str | None = None
    customer_id: int | None = None
    root_cause: str | None = None
    action: str | None = None
    reply: str | None = None
    status: int | None = None


@app.put("/api/complaints/{cid}")
def complaint_update(cid: int, body: ComplaintEditIn, token: str = Header(""),
                     db: Session = Depends(get_db)):
    """调查/回复/关闭：质量经理与管理员可处置；登记人可补充内容"""
    u = require_user(token, db)
    require_page(u, "complaint")
    c = db.get(M.Complaint, cid)
    if not c:
        raise HTTPException(404, "客诉单不存在")
    if body.status is not None and body.status not in (0, 1, 2, 3):
        raise HTTPException(400, "状态不合法")
    # 处置类字段（原因/措施/回复/状态推进）需要质量经理或管理员
    disposing = any([
        body.root_cause is not None, body.action is not None, body.reply is not None,
        body.status is not None,
    ])
    if disposing:
        _require_action(u, ["admin", "qm"])
    else:
        _require_action(u, ["admin", "qm", "qc", "buyer"])
    if body.claim_type is not None:
        if body.claim_type not in CLAIM_TYPES:
            raise HTTPException(400, "投诉类型不合法")
        c.claim_type = body.claim_type
    if body.severity is not None:
        if body.severity not in (1, 2, 3):
            raise HTTPException(400, "严重程度不合法")
        c.severity = body.severity
    for f in ("title", "content", "lot_no", "coa_no"):
        v = getattr(body, f)
        if v is not None:
            setattr(c, f, v.strip())
    if body.customer_id is not None:
        c.customer_id = body.customer_id
    if body.root_cause is not None:
        c.root_cause = body.root_cause.strip()
    if body.action is not None:
        c.action = body.action.strip()
    if body.reply is not None:
        c.reply = body.reply.strip()
    if body.status is not None:
        old = c.status
        c.status = body.status
        c.handled_by = u.username
        c.handled_at = datetime.now()
        if body.status == 3:
            c.closed_by = u.username
            c.closed_at = datetime.now()
            if not c.reply:
                raise HTTPException(400, "关闭客诉前请先填写对客户的回复内容")
        # 关闭前必须有原因分析与措施（8D 式闭环）
        if body.status == 3 and not (c.root_cause and c.action):
            raise HTTPException(400, "关闭前请先填写「原因分析」与「纠正措施」")
        audit(db, u.username, "update", f"complaint:{c.complaint_no}",
              f"状态 {COMPLAINT_STATUS.get(old)} → {COMPLAINT_STATUS.get(body.status)}")
    c.updated_at = datetime.now()
    db.commit()
    return {"ok": True, "status": c.status}


# ═══════════════════════ 登录锁定管理（管理员，第五批）═══════════════════════
@app.get("/api/admin/login-locks")
def login_locks(token: str = Header(""), db: Session = Depends(get_db)):
    """查看当前被锁定的账号/IP（公网暴露后运维排查用）"""
    u = require_user(token, db)
    require_page(u, "user")
    now = datetime.now()
    out = []
    for k, v in list(_LOGIN_FAILS.items()):
        if v[1] and v[1] > now:
            out.append({"type": "账号+IP", "key": k, "解锁时间": v[1].strftime("%Y-%m-%d %H:%M:%S")})
    for k, v in list(_IP_FAILS.items()):
        if v[1] and v[1] > now:
            out.append({"type": "IP", "key": k, "解锁时间": v[1].strftime("%Y-%m-%d %H:%M:%S")})
    return {"rows": out, "total": len(out),
            "policy": {"账号+IP 连错次数": LOGIN_MAX_FAILS, "锁定分钟": LOGIN_LOCK_MINUTES,
                       "同 IP 连错次数": IP_MAX_FAILS, "IP 锁定分钟": IP_LOCK_MINUTES}}


@app.post("/api/admin/login-locks/clear")
def login_locks_clear(key: str = "", token: str = Header(""), db: Session = Depends(get_db)):
    """解锁：key 为空清空全部；否则只清该 key（如 admin|1.2.3.4）"""
    u = require_user(token, db)
    require_page(u, "user")
    if key:
        _LOGIN_FAILS.pop(key, None)
        _IP_FAILS.pop(key, None)
        n = 1
    else:
        n = len(_LOGIN_FAILS) + len(_IP_FAILS)
        _LOGIN_FAILS.clear()
        _IP_FAILS.clear()
    audit(db, u.username, "update", "login-locks", f"解除登录锁定（{key or '全部'}）")
    db.commit()
    return {"ok": True, "cleared": n}


# ═══════════════════════ 操作日志（第三批）═══════════════════════
@app.get("/api/audit-logs")
def audit_logs(username: str = "", action: str = "", keyword: str = "", days: int = 30,
               limit: int = 300, token: str = Header(""), db: Session = Depends(get_db)):
    """操作日志：谁在什么时候改了什么（默认近 30 天，仅管理员可见）"""
    u = require_user(token, db)
    require_page(u, "audit")
    days = max(1, min(int(days), 365))
    since = datetime.now() - timedelta(days=days)
    q = db.query(M.AuditLog).filter(M.AuditLog.created_at >= since)
    if username:
        q = q.filter(M.AuditLog.username == username)
    if action:
        q = q.filter(M.AuditLog.action == action)
    if keyword:
        like = f"%{keyword}%"
        q = q.filter(or_(M.AuditLog.target.like(like), M.AuditLog.detail.like(like)))
    rows = q.order_by(M.AuditLog.id.desc()).limit(max(1, min(limit, 1000))).all()
    actions = [r[0] for r in db.query(M.AuditLog.action).distinct().all() if r[0]]
    users = [r[0] for r in db.query(M.AuditLog.username).distinct().all() if r[0]]
    return {"rows": [{"id": r.id, "username": r.username, "action": r.action,
                      "target": r.target, "detail": r.detail,
                      "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""}
                     for r in rows],
            "actions": sorted(actions), "users": sorted(users), "days": days}


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
              "sampler": "取样员", "prodlead": "班组长/主管", "buyer": "采购", "store": "仓储",
              "worker": "操作工"}


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
    attach_duty(x, db)
    _pos = db.get(M.Position, x.position_id) if x.position_id else None
    _dep = db.get(M.Department, x.dept_id) if x.dept_id else None
    return {"id": x.id, "username": x.username, "real_name": x.real_name,
            "department": x.department or (_dep.name if _dep else ""),
            "dept_id": x.dept_id, "dept_name": _dep.name if _dep else "",
            "position_id": x.position_id, "position_name": _pos.name if _pos else "",
            "perm_source": perm_source(x),
            "eff_pages": user_pages(x), "eff_manage": user_manage(x),
            "role": x.role_key,
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
    dept_id: int | None = None        # 所属部门（与职务一起决定岗位职责权限）
    position_id: int | None = None    # 职务
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
    _dep = db.get(M.Department, body.dept_id) if body.dept_id else None
    nu = M.User(username=body.username.strip(), real_name=body.real_name.strip(),
                department=(_dep.name if _dep else body.department.strip()),
                dept_id=body.dept_id, position_id=body.position_id,
                role_key=body.role_key,
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
    dept_id: int | None = None
    position_id: int | None = None
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
    if "dept_id" in body.model_fields_set:
        if body.dept_id:
            _dep = db.get(M.Department, body.dept_id)
            if not _dep:
                raise HTTPException(400, "部门不存在")
            m.dept_id = body.dept_id
            m.department = _dep.name          # 冗余显示名同步
        else:
            m.dept_id = None
    if "position_id" in body.model_fields_set:
        if body.position_id and not db.get(M.Position, body.position_id):
            raise HTTPException(400, "职务不存在")
        m.position_id = body.position_id
    if body.department is not None and "dept_id" not in body.model_fields_set:
        m.department = body.department
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
    import socket
    import uvicorn
    print("=" * 56)
    print("  生产质量管理系统 (QMS)")
    print("  本机访问:     http://localhost:8000")
    try:
        _s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _s.connect(("8.8.8.8", 80))
        _lan = _s.getsockname()[0]
        _s.close()
        print(f"  局域网/手机:  http://{_lan}:8000   （车间电脑、手机连同一网络即可打开）")
    except Exception:      # noqa: BLE001
        pass
    print("  演示账号: admin / qm / qc / prodlead / qc2 等, 密码 123456")
    print("  生产模式建议: uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4")
    print("=" * 56)
    uvicorn.run(app, host=os.environ.get("QMS_HOST", "0.0.0.0"),
            port=int(os.environ.get("QMS_PORT", "8000")), log_level="warning")
