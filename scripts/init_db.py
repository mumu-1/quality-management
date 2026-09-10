# -*- coding: utf-8 -*-
"""第1步：建库 + 全套演示数据（物料用用户提供的真实清单）"""
import hashlib, json, os, re, secrets, sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, engine, SessionLocal, ensure_schema
from models import (User, Material, Supplier, Customer, Workshop, Station,
                    Team, Equipment, QcStandard, QcStandardItem, UserStation,
                    IncomingLot, Sample, TestRecord, TestItem, Ncr,
                    ProductionLot, Coa, Department, Position, DutyTemplate, Complaint)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hash(pwd, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
    return salt, h



# 各工序"车间自检"指标（其余默认质检部检测）；键=工序编码，值=指标名
_SELF_ITEMS = {
    "ST01": ["溶液温度", "pH值"],                       # 温度计/试纸现场可测
    "ST03": ["反应温度", "反应时间"],                    # DCS 在线显示，车间可读
    "ST05": ["滤饼水分", "滤液澄清度"],                  # 快速水分仪/目测
    "ST06": ["出料温度", "外观颜色"],                    # 红外测温/目测
    "ST08": ["包装净含量"],                              # 电子秤
}


def seed_check_by(db):
    """给检验标准的每个指标分配检测方（幂等：库里还没有 self 项时才回填）"""
    db.flush()
    if db.query(QcStandardItem).filter(QcStandardItem.check_by == "self").count() > 0:
        return
    st_by_id = {s.id: s for s in db.query(Station).all()}
    n = 0
    for std in db.query(QcStandard).all():
        if std.object_type != "station":
            continue
        st = st_by_id.get(std.object_id)
        code = st.code if st else ""
        selfs = _SELF_ITEMS.get(code, [])
        for it in db.query(QcStandardItem).filter(QcStandardItem.standard_id == std.id).all():
            if it.indicator in selfs:
                it.check_by = "self"
                n += 1
    db.flush()
    print(f"✔ 检测方分配: 标为车间自检的指标 {n} 项（其余为质检部检测）")


def seed_standards(db):
    """第2步演示标准：已存在则跳过（幂等补种，可对第1步的库增量执行）"""
    if db.query(QcStandard).count() > 0:
        return
    mat = {m.code: m for m in db.query(Material).all()}
    st = {s.code: s for s in db.query(Station).all()}
    now = datetime.now()
    std_defs = [
        # 来料 IQC（按物料）
        ("iqc", "material", "RAW-001", "七水硫酸亚铁-来料检验标准", 1, [
            ("主含量(FeSO4·7H2O)", "%", 90, 103, "GB/T 10531 滴定法", 1),
            ("钛含量(Ti)", "%", None, 0.5, "ICP-OES", 0),
            ("锰含量(Mn)", "%", None, 0.5, "ICP-OES", 0),
            ("水不溶物", "%", None, 0.5, "GB/T 10531 重量法", 0),
            ("游离酸(以H2SO4计)", "%", None, 1.0, "酸碱滴定", 0),
        ]),
        ("iqc", "material", "RAW-002", "磷酸一铵-来料检验标准", 1, [
            ("总磷含量(P2O5)", "%", 61, None, "GB/T 10205 磷钼酸喹啉重量法", 1),
            ("氮含量(N)", "%", 11, None, "蒸馏滴定法", 1),
            ("水分", "%", None, 2.5, "烘干法", 0),
            ("水不溶物", "%", None, 0.3, "重量法", 0),
            ("粒度(1-4mm)", "%", 85, None, "筛分法", 0),
        ]),
        ("iqc", "material", "RAW-003", "85%磷酸-来料检验标准", 1, [
            ("磷酸含量(H3PO4)", "%", 85, None, "GB/T 2091 滴定法", 1),
            ("铁含量(Fe)", "%", None, 0.002, "ICP-OES", 0),
            ("氯化物(Cl)", "%", None, 0.0005, "比浊法", 0),
            ("硫酸盐(SO4)", "%", None, 0.005, "比浊法", 0),
            ("色度(黑曾)", "号", None, 20, "铂钴比色", 0),
        ]),
        ("iqc", "material", "RAW-004", "双氧水-来料检验标准", 1, [
            ("过氧化氢含量(H2O2)", "%", 27.5, None, "GB/T 1616 高锰酸钾滴定", 1),
            ("游离酸(以H2SO4计)", "%", None, 0.05, "酸碱滴定", 0),
            ("不挥发物", "%", None, 0.1, "蒸发残渣法", 0),
            ("稳定度", "%", 97, None, "GB/T 1616 加热法", 0),
        ]),
        ("iqc", "material", "AUX-001", "木质纤维素-来料检验标准", 1, [
            ("水分", "%", None, 8.0, "烘干法", 0),
            ("pH值(10%水悬液)", "", 5.5, 8.5, "pH计法", 0),
            ("纤维长度", "mm", 1.0, 5.0, "显微镜法", 0),
            ("外观", "", None, None, "目测：浅色纤维状，无结块", 0),
        ]),
        ("iqc", "material", "AUX-002", "硅藻土-来料检验标准", 1, [
            ("水分", "%", None, 3.0, "烘干法", 0),
            ("烧失量", "%", None, 5.0, "高温灼烧法", 0),
            ("pH值", "", 6.0, 9.0, "pH计法", 0),
            ("堆积密度", "g/L", 250, 450, "量筒法", 0),
            ("外观", "", None, None, "目测：白色粉末，无杂质", 0),
        ]),
        ("iqc", "material", "AUX-003", "硫酸-来料检验标准", 1, [
            ("硫酸含量(H2SO4)", "%", 98, None, "GB/T 534 滴定法", 1),
            ("铁含量(Fe)", "%", None, 0.005, "ICP-OES", 0),
            ("灰分", "%", None, 0.03, "灼烧法", 0),
            ("透明度", "mm", 50, None, "比色管法", 0),
        ]),
        # 过程 IPQC（按工序）
        ("ipqc", "station", "ST01", "溶解配液-过程检验标准", 1, [
            ("溶解浓度(FeSO4)", "g/L", 300, 400, "比重法", 1),
            ("溶液温度", "℃", 60, 85, "温度计", 0),
            ("pH值", "", 1.5, 3.0, "pH计法", 0),
        ]),
        ("ipqc", "station", "ST03", "合成反应-过程检验标准", 1, [
            ("反应温度", "℃", 88, 96, "DCS 温度计", 1),
            ("pH值", "", 1.8, 2.6, "在线pH计", 1),
            ("Fe/P摩尔比", "", 1.90, 2.10, "化学分析换算", 1),
            ("反应时间", "h", 2.0, 4.0, "DCS 计时", 0),
        ]),
        ("ipqc", "station", "ST05", "压滤洗涤-过程检验标准", 1, [
            ("滤饼水分", "%", None, 45, "烘干法", 0),
            ("洗涤水电导率", "μS/cm", None, 500, "电导率仪", 0),
            ("滤液澄清度", "", None, None, "目测：无明显浑浊", 0),
        ]),
        ("ipqc", "station", "ST06", "干燥脱水-过程检验标准", 1, [
            ("出料水分", "%", None, 1.5, "快速水分仪", 1),
            ("出料温度", "℃", None, 120, "红外测温", 0),
            ("外观颜色", "", None, None, "目测：浅黄至白色均匀粉末", 0),
        ]),
        ("ipqc", "station", "ST08", "除磁包装-过程检验标准", 1, [
            ("磁性异物", "ppb", None, 100, "磁吸法", 1),
            ("包装净含量", "kg", 24.5, 25.5, "电子秤", 0),
            ("封口质量", "", None, None, "目测：封口平整无泄漏", 0),
        ]),
    ]
    for i, (ctype, otype, ocode, name, sq, items) in enumerate(std_defs, start=1):
        obj_id = mat[ocode].id if otype == "material" else st[ocode].id
        std = QcStandard(std_no=f"STD-{i:03d}", name=name, object_type=otype,
                         object_id=obj_id, check_type=ctype, sample_qty=sq,
                         remark="", status=1, version=1, updated_by="admin",
                         created_at=now, updated_at=now)
        db.add(std); db.flush()
        for j, (ind, unit, lo, hi, method, key) in enumerate(items, start=1):
            db.add(QcStandardItem(standard_id=std.id, seq=j, indicator=ind, unit=unit,
                                  min_val=lo, max_val=hi, method=method, is_key=key))
    db.commit()
    print(f"✔ 检验标准库演示数据: {len(std_defs)} 套标准 "
          f"(来料 {sum(1 for s in std_defs if s[0]=='iqc')} / 过程 {sum(1 for s in std_defs if s[0]=='ipqc')})")


def seed_user_stations(db):
    """质检员负责多工序演示：幂等，仅在 user_station 无记录时种入"""
    db.flush()   # 关键：session autoflush=False，必须显式 flush 才能查到刚 add 的账号
    if db.query(UserStation).count() > 0:
        return
    st_map = {s.code: s.id for s in db.query(Station).all()}
    qc_multi = {
        "qc": ["ST03", "ST05"],     # 李检验 负责 合成反应+压滤洗涤
        "qc2": ["ST06", "ST08"],    # 王化验 负责 干燥脱水+除磁包装
    }
    n = 0
    for uname, st_codes in qc_multi.items():
        u = db.query(User).filter(User.username == uname).first()
        if u:
            for c in st_codes:
                db.add(UserStation(user_id=u.id, station_id=st_map[c]))
                n += 1
    db.commit()
    if n:
        print(f"✔ 质检员多工序绑定: 共 {n} 条 (qc=合成反应+压滤洗涤, qc2=干燥脱水+除磁包装)")


def seed_incoming(db):
    """第3步演示：来料批次（不同状态）+ 检验单 + NCR，幂等"""
    if db.query(IncomingLot).count() > 0:
        return
    mats = {m.code: m for m in db.query(Material).all()}
    sups = {s.code: s for s in db.query(Supplier).all()}
    stds = {s.name: s for s in db.query(QcStandard).all()}
    now = datetime.now()
    today = now.strftime("%Y%m%d")

    def find_std(mat_code):
        return next((s for s in stds.values()
                     if s.object_type == "material" and s.object_id == mats[mat_code].id
                     and s.check_type == "iqc"), None)

    def std_items(sid):
        return [it for it in db.query(QcStandardItem)
                .filter(QcStandardItem.standard_id == sid, QcStandardItem.enabled == 1)
                .order_by(QcStandardItem.seq).all()]

    # 批次1：七水硫酸亚铁 已合格放行
    lot1 = IncomingLot(lot_no=f"{today}-RA-SUP01-001", material_id=mats["RAW-001"].id,
                       supplier_id=sups["SUP-001"].id, supplier_lot="GY-2201",
                       qty=32.5, unit="t", vehicle="鄂A12345", status=2,
                       arrival_by="buyer", created_at=now, updated_at=now)
    db.add(lot1); db.flush()
    sp = Sample(sample_no=f"SP-{today}-001", lot_id=lot1.id, sample_by="sampler", status=1)
    db.add(sp); db.flush()
    std1 = find_std("RAW-001")
    tr1 = TestRecord(test_no=f"T-{today}-001", lot_id=lot1.id, sample_id=sp.id,
                     std_id=std1.id, check_type="iqc", result=1, tested_by="qc")
    db.add(tr1); db.flush()
    for i, it in enumerate(std_items(std1.id), start=1):
        vals = {"主含量(FeSO4·7H2O)": "95.2", "钛含量(Ti)": "0.21", "锰含量(Mn)": "0.18",
                "水不溶物": "0.22", "游离酸(以H2SO4计)": "0.45"}
        db.add(TestItem(test_id=tr1.id, seq=i, indicator=it.indicator, unit=it.unit,
                        min_val=it.min_val, max_val=it.max_val, method=it.method,
                        is_key=it.is_key, actual=vals.get(it.indicator, ""), pass_flag=1))

    # 批次2：磷酸一铵 待取样（新到货）
    lot2 = IncomingLot(lot_no=f"{today}-RA-SUP02-001", material_id=mats["RAW-002"].id,
                       supplier_id=sups["SUP-002"].id, supplier_lot="HB-8803",
                       qty=25, unit="t", vehicle="鄂B67890", status=0,
                       arrival_by="buyer", created_at=now, updated_at=now)
    db.add(lot2)

    # 批次3：85%磷酸 待检验（已取样）
    lot3 = IncomingLot(lot_no=f"{today}-RA-SUP02-002", material_id=mats["RAW-003"].id,
                       supplier_id=sups["SUP-002"].id, supplier_lot="HB-8804",
                       qty=20, unit="t", vehicle="鄂B67891", status=1,
                       arrival_by="buyer", created_at=now, updated_at=now)
    db.add(lot3); db.flush()
    sp3 = Sample(sample_no=f"SP-{today}-002", lot_id=lot3.id, sample_by="sampler", status=0)
    db.add(sp3)

    # 批次4：木质纤维素 不合格→NCR 已让步接收（演示历史）
    lot4 = IncomingLot(lot_no=f"{today}-RA-SUP04-001", material_id=mats["AUX-001"].id,
                       supplier_id=sups["SUP-004"].id, supplier_lot="SC-3302",
                       qty=3.2, unit="t", vehicle="川C11122", status=4,
                       arrival_by="buyer", created_at=now, updated_at=now)
    db.add(lot4); db.flush()
    sp4 = Sample(sample_no=f"SP-{today}-003", lot_id=lot4.id, sample_by="sampler", status=1)
    db.add(sp4); db.flush()
    std4 = find_std("AUX-001")
    tr4 = TestRecord(test_no=f"T-{today}-002", lot_id=lot4.id, sample_id=sp4.id,
                     std_id=std4.id, check_type="iqc", result=2, tested_by="qc")
    db.add(tr4); db.flush()
    for i, it in enumerate(std_items(std4.id), start=1):
        # 水分 8.0 上限 → 故意超 0.3 → 不合格
        bad = "9.1" if it.indicator == "水分" else ("6.8" if it.indicator == "pH值(10%水悬液)" else "合格")
        is_bad = it.indicator in ("水分",)
        db.add(TestItem(test_id=tr4.id, seq=i, indicator=it.indicator, unit=it.unit,
                        min_val=it.min_val, max_val=it.max_val, method=it.method,
                        is_key=it.is_key, actual=bad,
                        pass_flag=0 if is_bad else 1))
    ncr4 = Ncr(ncr_no=f"NCR-{now.year}-{now.month:02d}001", lot_id=lot4.id, test_id=tr4.id,
               fail_summary="水分 实测9.1（标准≤8.0）超出标准上限 8.0",
               disposition="waive", status=2, created_by="qc", handled_by="qm",
               handled_at=now, remark="季节影响，让步接收用于低端批次", created_at=now)
    db.add(ncr4)

    # 批次5：硫酸 不合格→NCR 待处理（演示当前待办）
    lot5 = IncomingLot(lot_no=f"{today}-RA-SUP03-001", material_id=mats["AUX-003"].id,
                       supplier_id=sups["SUP-003"].id, supplier_lot="YN-5510",
                       qty=15, unit="t", vehicle="云D33445", status=3,
                       arrival_by="buyer", created_at=now, updated_at=now)
    db.add(lot5); db.flush()
    sp5 = Sample(sample_no=f"SP-{today}-004", lot_id=lot5.id, sample_by="sampler", status=1)
    db.add(sp5); db.flush()
    std5 = find_std("AUX-003")
    tr5 = TestRecord(test_no=f"T-{today}-003", lot_id=lot5.id, sample_id=sp5.id,
                     std_id=std5.id, check_type="iqc", result=2, tested_by="qc2")
    db.add(tr5); db.flush()
    for i, it in enumerate(std_items(std5.id), start=1):
        bad = "96.8" if it.indicator == "硫酸含量(H2SO4)" else ("0.06" if it.indicator == "铁含量(Fe)" else "合格")
        is_bad = it.indicator in ("硫酸含量(H2SO4)",)
        db.add(TestItem(test_id=tr5.id, seq=i, indicator=it.indicator, unit=it.unit,
                        min_val=it.min_val, max_val=it.max_val, method=it.method,
                        is_key=it.is_key, actual=bad,
                        pass_flag=0 if is_bad else 1))
    db.add(Ncr(ncr_no=f"NCR-{now.year}-{now.month:02d}002", lot_id=lot5.id, test_id=tr5.id,
               fail_summary="硫酸含量(H2SO4) 实测96.8（标准≥98）低于标准下限 98.0",
               disposition="", status=0, created_by="qc2", created_at=now))
    db.commit()
    print(f"✔ 来料检验演示数据: 5 批来料（放行1/待取样1/待检验1/让步1/冻结待处理1）+ 检验单3 + NCR2")



def seed_production(db):
    """第4步演示：成品OQC标准 + 生产主链（原料→溶解→合成→压滤→干燥→包装成品）+ COA
    幂等：production_lot 已有数据则跳过"""
    db.flush()
    if db.query(ProductionLot).count() > 0:
        return
    # 幂等补成品物料（老库升级时 FG 可能不存在）
    if not db.query(Material).filter(Material.code == "FG-001").first():
        db.add(Material(code="FG-001", name="电池级磷酸铁", material_type="成品",
                        spec="FePO4 电池级 D50 1~3um", unit="t"))
    if not db.query(Material).filter(Material.code == "FG-002").first():
        db.add(Material(code="FG-002", name="工业级磷酸铁", material_type="成品",
                        spec="FePO4 工业级", unit="t"))
    db.flush()
    mats = {m.code: m for m in db.query(Material).all()}
    sts = {st.code: st for st in db.query(Station).all()}
    eqs = {e.code: e for e in db.query(Equipment).all()}
    teams = {t.name: t for t in db.query(Team).all()}
    now = datetime.now()
    today = now.strftime("%Y%m%d")
    tseq = [100]   # 检验单号自增种子（避开 seed_incoming 已用的 001-00x）

    def get_std(otype, oid, ctype, name):
        std = db.query(QcStandard).filter(QcStandard.object_type == otype,
                                          QcStandard.object_id == oid,
                                          QcStandard.check_type == ctype).first()
        if std:
            return std
        db.flush()   # 关键：让本次会话已 add 的标准可见，避免连续新建取到同一号
        mx = db.query(QcStandard).order_by(QcStandard.id.desc()).first()
        no = f"STD-{(int(mx.std_no.split('-')[1]) + 1) if mx else 1:03d}"
        std = QcStandard(std_no=no, name=name, object_type=otype, object_id=oid,
                         check_type=ctype, sample_qty=1, status=1, version=1,
                         updated_by="admin", created_at=now, updated_at=now)
        db.add(std)
        return std

    def mk_items(std, rows):
        db.flush()
        for j, (ind, unit, lo, hi, method, key) in enumerate(rows, start=1):
            db.add(QcStandardItem(standard_id=std.id, seq=j, indicator=ind, unit=unit,
                                  min_val=lo, max_val=hi, method=method, is_key=key))

    # ── 成品 OQC 标准（FG-001 电池级 / FG-002 工业级）──
    fg1 = mats.get("FG-001")
    fg2 = mats.get("FG-002")
    oqc1 = get_std("material", fg1.id, "oqc", "电池级磷酸铁-成品检验标准") if fg1 else None
    oqc2 = get_std("material", fg2.id, "oqc", "工业级磷酸铁-成品检验标准") if fg2 else None
    # 电池级标准项（Fe/P 摩尔比 0.96~1.00 关键、Fe≥29、P 16~17、水分≤0.5、D50 1~3μm、磁性异物≤100ppb、外观）
    if oqc1 and db.query(QcStandardItem).filter(QcStandardItem.standard_id == oqc1.id).count() == 0:
        mk_items(oqc1, [
            ("铁含量(Fe)", "%", 29.0, None, "ICP-OES", 1),
            ("磷含量(P)", "%", 16.0, 17.0, "ICP-OES", 1),
            ("Fe/P摩尔比", "", 0.96, 1.00, "计算", 1),
            ("水分", "%", None, 0.5, "卡氏水分仪", 0),
            ("粒径D50", "um", 1.0, 3.0, "激光粒度仪", 1),
            ("磁性异物", "ppb", None, 100, "磁吸法", 1),
            ("外观", "", None, None, "目测：白色至浅黄粉末无结块", 0),
        ])
    if oqc2 and db.query(QcStandardItem).filter(QcStandardItem.standard_id == oqc2.id).count() == 0:
        mk_items(oqc2, [
            ("铁含量(Fe)", "%", 28.0, None, "ICP-OES", 1),
            ("磷含量(P)", "%", 15.5, 17.5, "ICP-OES", 1),
            ("Fe/P摩尔比", "", 0.94, 1.02, "计算", 1),
            ("水分", "%", None, 1.0, "卡氏水分仪", 0),
            ("粒径D50", "um", 1.0, 5.0, "激光粒度仪", 0),
            ("外观", "", None, None, "目测：无结块无异物", 0),
        ])
    db.flush()

    # ── 生产主链演示（父批约束：上工序合格批）──
    # 父原料 = seed_incoming 的批次1 七水硫酸亚铁（status=2 合格放行）
    parent_in = db.query(IncomingLot).filter(IncomingLot.status == 2).first()
    if not parent_in:
        db.commit()
        return

    def std_items_map(std):
        return {it.indicator: it for it in db.query(QcStandardItem)
                .filter(QcStandardItem.standard_id == std.id, QcStandardItem.enabled == 1).all()}

    def good_actual(it):
        if it.min_val is not None and it.max_val is not None:
            return str(round((it.min_val + it.max_val) / 2, 2))
        if it.min_val is not None:
            return str(it.min_val + (it.max_val - it.min_val if it.max_val is not None else 0.05))
        if it.max_val is not None:
            return str(round(it.max_val * 0.5, 2))
        return "合格"

    def bad_actual(it):
        if it.min_val is not None and it.max_val is not None:
            return str(it.min_val - 0.05)
        if it.min_val is not None:
            return str(round(it.min_val - 0.05, 2))
        if it.max_val is not None:
            return str(round(it.max_val + 0.05, 2))
        return ""

    def make_test(prod_id, std, tested_by, fail=False):
        """生成检验单+明细；fail=True 时首项故意不合格"""
        tseq[0] += 1
        tr = TestRecord(test_no=f"T-{today}-{tseq[0]:03d}", prod_id=prod_id, std_id=std.id,
                        check_type=std.check_type, result=1 if not fail else 2, tested_by=tested_by)
        db.add(tr); db.flush()
        for i, it in enumerate(std_items_map(std).values(), start=1):
            bad = fail and i == 1
            actual = bad_actual(it) if bad else good_actual(it)
            db.add(TestItem(test_id=tr.id, seq=i, indicator=it.indicator, unit=it.unit,
                            min_val=it.min_val, max_val=it.max_val, method=it.method,
                            is_key=it.is_key, actual=actual,
                            pass_flag=0 if bad else 1))
        db.flush()   # 让明细落库，调用方可立即查询
        return tr

    # 批号格式：日期-ST工序-EQ设备-序号-[父引用短码]-班组（完整父批号单独存 parent_lot_no 字段）
    def _short_ref(parent_no):
        m = re.search(r"(ST\d+-EQ\d+-\d{3}|RA-[A-Z0-9]+-\d{3})", parent_no or "")
        return m.group(1) if m else (parent_no or "")[-16:]

    def lot_no(st_code, eq_code, seq, parent_no, team):
        return f"{today}-ST{st_code[-2:]}-{eq_code}-{seq:03d}-[{_short_ref(parent_no)}]-{team}"

    seq_ctr = {}

    def next_seq(key):
        seq_ctr[key] = seq_ctr.get(key, 0) + 1
        return seq_ctr[key]

    # P1: 溶解配液 ST01（父=原料批）
    p1 = ProductionLot(lot_no=lot_no("ST01", "EQ001", next_seq("ST01"), parent_in.lot_no, "甲"),
                       station_id=sts["ST01"].id, equipment_id=eqs["EQ-001"].id,
                       team_id=teams["甲班"].id, material_id=mats["RAW-001"].id,
                       parent_type="incoming", parent_lot_no=parent_in.lot_no,
                       qty=30.0, unit="t", status=2, operator="prodlead2",
                       created_at=now, updated_at=now)
    db.add(p1); db.flush()
    ipqc1 = get_std("station", sts["ST01"].id, "ipqc", "溶解配液-过程检验标准")
    make_test(p1.id, ipqc1, "qc")

    # P2: 合成反应 ST03（父=P1）
    p2 = ProductionLot(lot_no=lot_no("ST03", "EQ004", next_seq("ST03"), p1.lot_no, "甲"),
                       station_id=sts["ST03"].id, equipment_id=eqs["EQ-004"].id,
                       team_id=teams["甲班"].id,
                       parent_type="production", parent_lot_no=p1.lot_no,
                       qty=28.0, unit="t", status=2, operator="prodlead",
                       created_at=now, updated_at=now)
    db.add(p2); db.flush()
    ipqc3 = get_std("station", sts["ST03"].id, "ipqc", "合成反应-过程检验标准")
    make_test(p2.id, ipqc3, "qc")

    # P3: 压滤洗涤 ST05（父=P2）
    p3 = ProductionLot(lot_no=lot_no("ST05", "EQ007", next_seq("ST05"), p2.lot_no, "乙"),
                       station_id=sts["ST05"].id, equipment_id=eqs["EQ-007"].id,
                       team_id=teams["乙班"].id,
                       parent_type="production", parent_lot_no=p2.lot_no,
                       qty=26.0, unit="t", status=2, operator="prodlead",
                       created_at=now, updated_at=now)
    db.add(p3); db.flush()
    ipqc5 = get_std("station", sts["ST05"].id, "ipqc", "压滤洗涤-过程检验标准")
    make_test(p3.id, ipqc5, "qc")

    # P4: 干燥脱水 ST06（父=P3）
    p4 = ProductionLot(lot_no=lot_no("ST06", "EQ009", next_seq("ST06"), p3.lot_no, "乙"),
                       station_id=sts["ST06"].id, equipment_id=eqs["EQ-009"].id,
                       team_id=teams["乙班"].id,
                       parent_type="production", parent_lot_no=p3.lot_no,
                       qty=25.0, unit="t", status=2, operator="prodlead",
                       created_at=now, updated_at=now)
    db.add(p4); db.flush()
    ipqc6 = get_std("station", sts["ST06"].id, "ipqc", "干燥脱水-过程检验标准")
    make_test(p4.id, ipqc6, "qc2")

    # P5: 除磁包装 ST08（父=P4，产出成品 FG-001）→ IPQC 合格 + OQC 合格 + COA
    p5 = ProductionLot(lot_no=lot_no("ST08", "EQ013", next_seq("ST08"), p4.lot_no, "丙"),
                       station_id=sts["ST08"].id, equipment_id=eqs["EQ-013"].id,
                       team_id=teams["丙班"].id, material_id=mats["FG-001"].id,
                       parent_type="production", parent_lot_no=p4.lot_no,
                       qty=24.0, unit="t", status=5, operator="prodlead",
                       created_at=now, updated_at=now)
    db.add(p5); db.flush()
    ipqc8 = get_std("station", sts["ST08"].id, "ipqc", "除磁包装-过程检验标准")
    tr_ipqc8 = make_test(p5.id, ipqc8, "qc2")
    tr_oqc = make_test(p5.id, oqc1, "qc2")
    # COA 明细快照：与后端 _issue_coa 一致（此后 OQC 标准改动不影响历史报告）
    snap = [{"indicator": ti.indicator, "actual": ti.actual, "unit": ti.unit,
             "min_val": ti.min_val, "max_val": ti.max_val, "pass": ti.pass_flag}
            for ti in db.query(TestItem).filter(TestItem.test_id == tr_oqc.id)
               .order_by(TestItem.seq).all()]
    db.add(Coa(coa_no=f"COA-{today}-{next_seq('COA'):03d}", prod_id=p5.id,
               customer_id=db.query(Customer).filter(Customer.code == "CUS-001").first().id,
               test_id=tr_oqc.id,
               items_json=json.dumps(snap, ensure_ascii=False),
               result=1, issued_by="qm", created_at=now))

    # P6: ST05 再来一批（父=P2）→ 故意 IPQC 不合格 → 冻结 + NCR(prod)
    p6 = ProductionLot(lot_no=lot_no("ST05", "EQ008", next_seq("ST05"), p2.lot_no, "乙"),
                       station_id=sts["ST05"].id, equipment_id=eqs["EQ-008"].id,
                       team_id=teams["乙班"].id,
                       parent_type="production", parent_lot_no=p2.lot_no,
                       qty=12.0, unit="t", status=3, operator="prodlead",
                       created_at=now, updated_at=now)
    db.add(p6); db.flush()
    tr6 = make_test(p6.id, ipqc5, "qc", fail=True)
    # 取第一个不合格项的说明
    fail_it = db.query(TestItem).filter(TestItem.test_id == tr6.id, TestItem.pass_flag == 0).first()
    db.add(Ncr(ncr_no=f"NCR-{now.year}-{now.month:02d}003", prod_id=p6.id, test_id=tr6.id,
               fail_summary=f"{fail_it.indicator} 实测{fail_it.actual}"
                            f"（标准{'≥' + str(fail_it.min_val) if fail_it.min_val is not None else ''}"
                            f"{'≤' + str(fail_it.max_val) if fail_it.max_val is not None else ''}）",
               status=0, created_by="qc", created_at=now))

    # P7: ST03 再来一批（父=P1）→ 待过程检验（演示"去检验"入口）
    p7 = ProductionLot(lot_no=lot_no("ST03", "EQ005", next_seq("ST03"), p1.lot_no, "丙"),
                       station_id=sts["ST03"].id, equipment_id=eqs["EQ-005"].id,
                       team_id=teams["丙班"].id,
                       parent_type="production", parent_lot_no=p1.lot_no,
                       qty=10.0, unit="t", status=1, operator="prodlead",
                       created_at=now, updated_at=now)
    db.add(p7)
    db.commit()
    print(f"✔ 生产批次演示数据: 主链6批(原料→包装成品)+OQC标准2套+COA1张+待检1批+不合格冻结1批(含NCR)")



def seed_history(db):
    """第5步演示：近30天历史检验数据（供报表/SPC/大屏）
    - 8 条完整生产链(每条 5 工序)，其中 2 条在中途工序不合格 → 冻结 + NCR
    - 同一工序同一指标 ≥6 个数值点；并故意造 2 个"超控制限(仍在标准内)"的失控点
    - 幂等：以 remark='HIST' 标记是否存在
    注意：随机数用固定种子，保证结果可复现。"""
    import random
    rnd = random.Random(20260910)
    db.flush()
    if db.query(ProductionLot).filter(ProductionLot.remark == "HIST").count() > 0:
        return
    sts = {st.code: st for st in db.query(Station).all()}
    eqs = {}
    for e in db.query(Equipment).all():
        eqs.setdefault(e.station_id, []).append(e)
    teams = db.query(Team).all()
    mats = {m.code: m for m in db.query(Material).all()}
    sups = {sp.code: sp for sp in db.query(Supplier).all()}
    now = datetime.now()
    tseq = [300]

    def next_test_no():
        tseq[0] += 1
        return f"T-{now.strftime('%Y%m%d')}-{tseq[0]:03d}"

    def std_of(otype, oid, ctype):
        return db.query(QcStandard).filter(QcStandard.object_type == otype,
                                          QcStandard.object_id == oid,
                                          QcStandard.check_type == ctype).first()

    def items_of(std):
        return db.query(QcStandardItem).filter(QcStandardItem.standard_id == std.id,
                                              QcStandardItem.enabled == 1) \
            .order_by(QcStandardItem.seq).all()

    def value_for(it, mode="normal"):
        """按标准生成实测值；mode: normal=正常波动, bad=不合格, drift=超控制限(仍在标准内)"""
        lo, hi = it.min_val, it.max_val
        if lo is None and hi is None:
            return "合格"
        if lo is not None and hi is not None:
            span = hi - lo
            if mode == "bad":
                return str(round(lo - span * 0.15, 3))
            if mode == "drift":
                # 冲到规格上限（仍然合格），因正常波动仅 ±1% 量程 → 该点必然超出控制限
                return str(round(hi - span * 0.005, 3))
            return str(round(lo + span * (0.49 + rnd.random() * 0.02), 3))
        if lo is not None:                                     # 只有下限
            if mode == "bad":
                return str(round(lo - abs(lo) * 0.05 - 0.2, 2))
            if mode == "drift":
                return str(round(lo + abs(lo) * 0.12, 3))
            return str(round(lo + abs(lo) * (0.02 + rnd.random() * 0.008), 3))
        if mode == "bad":                                      # 只有上限
            return str(round(hi + abs(hi) * 0.1 + 0.1, 2))
        if mode == "drift":
            return str(round(hi * 0.995, 3))
        return str(round(hi * (0.5 + rnd.random() * 0.02), 3))

    def make_test(prod_id, std, by, mode="normal", day_offset=0, hour=10):
        tr = TestRecord(test_no=next_test_no(), prod_id=prod_id, std_id=std.id,
                        check_type=std.check_type, result=0, tested_by=by,
                        created_at=now - timedelta(days=day_offset, hours=rnd.randint(0, 6)))
        db.add(tr)
        db.flush()
        fail = 0
        for i, it in enumerate(items_of(std), start=1):
            m = mode if (mode == "bad" and i == 1) else ("drift" if (mode == "drift" and i == 1) else "normal")
            v = value_for(it, m)
            passed = 1
            if it.min_val is not None or it.max_val is not None:
                try:
                    n = float(v)
                    passed = 1 if ((it.min_val is None or n >= it.min_val) and
                                   (it.max_val is None or n <= it.max_val)) else 0
                except ValueError:
                    passed = 1
            elif not v:
                passed = 0
            if not passed:
                fail += 1
            db.add(TestItem(test_id=tr.id, seq=i, indicator=it.indicator, unit=it.unit,
                            min_val=it.min_val, max_val=it.max_val, method=it.method,
                            is_key=it.is_key, actual=v, pass_flag=passed))
        tr.result = 2 if fail else 1
        db.flush()
        return tr

    def make_test_in(dbi, lot_id, std, by, mode, day_offset):
        tr = TestRecord(test_no=next_test_no(), lot_id=lot_id, std_id=std.id,
                        check_type="iqc", result=0, tested_by=by,
                        created_at=now - timedelta(days=day_offset, hours=rnd.randint(0, 6)))
        dbi.add(tr)
        dbi.flush()
        fail = 0
        for i, it in enumerate(items_of(std), start=1):
            m = mode if (mode == "bad" and i == 1) else "normal"
            v = value_for(it, m)
            passed = 1
            if it.min_val is not None or it.max_val is not None:
                try:
                    n = float(v)
                    passed = 1 if ((it.min_val is None or n >= it.min_val) and
                                   (it.max_val is None or n <= it.max_val)) else 0
                except ValueError:
                    passed = 1
            if not passed:
                fail += 1
            dbi.add(TestItem(test_id=tr.id, seq=i, indicator=it.indicator, unit=it.unit,
                             min_val=it.min_val, max_val=it.max_val, method=it.method,
                             is_key=it.is_key, actual=v, pass_flag=passed))
        tr.result = 2 if fail else 1
        dbi.flush()
        return tr

    # ── 原料批：8 个合格 + 1 个不合格（供 IQC 合格率与柏拉图）──
    raw_defs = [("RAW-001", "SUP-001"), ("RAW-002", "SUP-002"), ("RAW-003", "SUP-002"),
                ("RAW-004", "SUP-003"), ("RAW-001", "SUP-001"), ("AUX-001", "SUP-004"),
                ("RAW-002", "SUP-002"), ("RAW-003", "SUP-002"), ("RAW-001", "SUP-001")]
    raw_lots = []
    for k, (mc, sc) in enumerate(raw_defs, start=1):
        d = now - timedelta(days=28 - k * 3)
        bad = (k == 9)      # 最后一批故意不合格
        sup = sups[sc]
        digits = "".join(ch for ch in sup.code if ch.isdigit())[-2:].zfill(2)
        lot = IncomingLot(lot_no=f"{d.strftime('%Y%m%d')}-RA-SUP{digits}-{k:03d}",
                          material_id=mats[mc].id, supplier_id=sup.id,
                          supplier_lot=f"HIS-{k:04d}", qty=round(20 + rnd.random() * 15, 1),
                          unit="t", vehicle="鄂A" + str(10000 + k), status=1,
                          arrival_by="buyer", created_at=d, updated_at=d)
        db.add(lot)
        db.flush()
        std = db.query(QcStandard).filter(QcStandard.object_type == "material",
                                         QcStandard.object_id == mats[mc].id,
                                         QcStandard.check_type == "iqc").first()
        if std:
            tr = make_test_in(db, lot.id, std, "qc", "bad" if bad else "normal", 28 - k * 3)
            lot.status = 3 if bad else 2
            if bad:
                it0 = db.query(TestItem).filter(TestItem.test_id == tr.id,
                                                TestItem.pass_flag == 0).first()
                db.add(Ncr(ncr_no=f"NCR-{now.year}-H{k:03d}", lot_id=lot.id, test_id=tr.id,
                           fail_summary=f"{it0.indicator} 实测{it0.actual}（超标准）" if it0 else "不合格",
                           status=0 if k == 9 else 1, created_by="qc", created_at=d))
        db.flush()
        raw_lots.append(lot)


    # ── 8 条生产链（每条 5 工序）；第 4、7 条中途不合格 ──
    chain_codes = ["ST01", "ST03", "ST05", "ST06", "ST08"]
    n_coa = 0
    for c in range(1, 9):
        day0 = 26 - c * 3
        parent_no = raw_lots[(c - 1) % 8].lot_no
        broken = c in (4, 7)
        for si, code in enumerate(chain_codes):
            st = sts[code]
            eq = eqs[st.id][c % len(eqs[st.id])]
            team = teams[c % len(teams)]
            d = now - timedelta(days=max(day0 - si, 0))
            digits = "".join(ch for ch in eq.code if ch.isdigit())[-2:].zfill(2)
            lot_no = f"{d.strftime('%Y%m%d')}-ST{code[-2:]}-EQ{digits}-{c:03d}-[HIS]-{team.name[0]}"
            is_fg = code == "ST08"
            pl = ProductionLot(lot_no=lot_no, station_id=st.id, equipment_id=eq.id,
                               team_id=team.id,
                               material_id=mats["FG-001"].id if is_fg else None,
                               parent_type="incoming" if si == 0 else "production",
                               parent_lot_no=parent_no, qty=round(18 + rnd.random() * 8, 1),
                               unit="t", status=1, operator="prodlead",
                               remark="HIST", created_at=d, updated_at=d)
            db.add(pl)
            db.flush()
            # 过程检验：broken 链在第 2 道工序(ST03)不合格
            bad_here = broken and si == 1
            # SPC 失控点：仅在 c==1 这条链的 ST01/ST03/ST05 各造 1 个"超控制限但仍在标准内"的点
            # （同一指标只放 1 个异常点，否则多个异常点会把控制限抬宽而落在限内）
            drift_here = (c == 1) and (si in (0, 1, 2))
            std = std_of("station", st.id, "ipqc")
            if std:
                make_test(pl.id, std, "qc" if si % 2 == 0 else "qc2",
                          "bad" if bad_here else ("drift" if drift_here else "normal"),
                          max(day0 - si, 0))
            if bad_here:
                pl.status = 3
                it0 = db.query(TestItem).join(TestRecord, TestItem.test_id == TestRecord.id) \
                    .filter(TestRecord.prod_id == pl.id, TestItem.pass_flag == 0).first()
                db.add(Ncr(ncr_no=f"NCR-{now.year}-H1{c:02d}",
                           prod_id=pl.id, test_id=None,
                           fail_summary=f"{it0.indicator} 实测{it0.actual}（超标准）" if it0 else "过程不合格",
                           status=0, created_by="qc", created_at=d))
                break                                       # 链在此中断
            if is_fg:
                # 成品 OQC 合格 → COA
                oq = std_of("material", mats["FG-001"].id, "oqc")
                tr_o = make_test(pl.id, oq, "qc2", "normal", max(day0 - si, 0))
                pl.status = 5
                snap = [{"indicator": ti.indicator, "actual": ti.actual, "unit": ti.unit,
                         "min_val": ti.min_val, "max_val": ti.max_val, "pass": ti.pass_flag}
                        for ti in db.query(TestItem).filter(TestItem.test_id == tr_o.id)
                           .order_by(TestItem.seq).all()]
                n_coa += 1
                db.add(Coa(coa_no=f"COA-{d.strftime('%Y%m%d')}-H{n_coa:02d}", prod_id=pl.id,
                           test_id=tr_o.id, items_json=json.dumps(snap, ensure_ascii=False),
                           result=1, issued_by="qm", created_at=d))
            else:
                pl.status = 2
            parent_no = lot_no
    db.commit()
    print(f"✔ 历史数据: 来料9批(含1不合格) + 生产链8条({sum(1 for p in db.query(ProductionLot).filter(ProductionLot.remark=='HIST') if p.status==5)}条到成品) + COA{n_coa}张 + SPC含失控点")



# ═══════════════ 部门 / 职务 / 岗位职责（分权责：部门+职务 → 模块权限）═══════════════
PAGES_ALL = ["dashboard", "screen", "board", "prodlot", "qcstandard", "incoming", "ncr",
             "trace", "report", "complaint", "material", "supplier", "customer", "workshop",
             "station", "team", "equipment", "department", "position", "user", "duty", "audit"]
MANAGE_ALL = ["material", "supplier", "customer", "workshop", "station", "team", "equipment",
              "department", "position", "user", "duty", "audit", "qcstandard", "incoming", "ncr",
              "prodlot", "complaint"]

_DEPTS = [
    ("DEPT-GM", "总经办", 1), ("DEPT-IT", "信息部", 2), ("DEPT-QC", "质量部", 3),
    ("DEPT-PR", "生产部", 4), ("DEPT-PU", "采购部", 5), ("DEPT-WH", "仓储部", 6),
    ("DEPT-EQ", "设备部", 7), ("DEPT-TC", "技术部", 8),
]
_POSITIONS = [
    ("POS-GM", "总经理", 1), ("POS-ADMIN", "系统管理员", 2), ("POS-MGR-QC", "质量经理", 3),
    ("POS-SUP-QC", "质量主管", 4), ("POS-QC", "检验员", 5), ("POS-LAB", "化验员", 6),
    ("POS-SAMPLER", "取样员", 7), ("POS-DIR", "车间主任", 8), ("POS-PL", "班组长", 9),
    ("POS-OP", "操作工", 10), ("POS-PU-MGR", "采购经理", 11), ("POS-BUYER", "采购员", 12),
    ("POS-WH-MGR", "仓储主管", 13), ("POS-KEEPER", "库管", 14), ("POS-EQ-MGR", "设备主管", 15),
    ("POS-FIXER", "维修工", 16), ("POS-TECH", "技术员", 17),
]

_BASE = ["material", "supplier", "customer", "workshop", "station", "team", "equipment",
         "department", "position"]
_QC = ["dashboard", "board", "screen", "prodlot", "qcstandard", "incoming", "ncr", "trace",
       "report", "complaint"]
_QC_LINE = ["dashboard", "screen", "prodlot", "qcstandard", "incoming", "ncr", "trace",
            "report", "complaint", "material", "equipment"]

# (部门, 职务, 可见模块, 可管模块)；部门 None = 通用职务兜底
_DUTIES = [
    ("DEPT-IT", "POS-ADMIN", PAGES_ALL, MANAGE_ALL),
    ("DEPT-GM", "POS-GM", _QC + _BASE, []),
    ("DEPT-QC", "POS-MGR-QC", _QC + _BASE,
     ["qcstandard", "incoming", "ncr", "prodlot", "material", "supplier", "customer",
      "workshop", "station", "team", "equipment", "department", "position"]),
    ("DEPT-QC", "POS-SUP-QC", _QC + _BASE,
     ["qcstandard", "incoming", "ncr", "prodlot", "material", "equipment"]),
    ("DEPT-QC", "POS-QC", _QC_LINE, []),
    ("DEPT-QC", "POS-LAB", _QC_LINE, []),
    ("DEPT-QC", "POS-SAMPLER", ["dashboard", "incoming", "material"], []),
    ("DEPT-PR", "POS-DIR", ["dashboard", "screen", "board", "prodlot", "qcstandard", "trace",
                            "report", "workshop", "station", "team", "equipment", "material"],
     ["prodlot"]),
    ("DEPT-PR", "POS-PL", ["dashboard", "screen", "prodlot", "qcstandard", "trace",
                           "workshop", "station", "equipment", "team"], ["prodlot"]),
    ("DEPT-PR", "POS-OP", ["dashboard", "prodlot"], []),
    ("DEPT-PU", "POS-PU-MGR", ["dashboard", "incoming", "ncr", "trace", "complaint", "material",
                               "supplier", "customer"], ["supplier", "incoming", "ncr"]),
    ("DEPT-PU", "POS-BUYER", ["dashboard", "incoming", "ncr", "trace", "complaint", "material",
                              "supplier", "customer"], ["supplier", "incoming", "ncr"]),
    ("DEPT-WH", "POS-WH-MGR", ["dashboard", "incoming", "ncr", "trace", "material",
                               "customer", "workshop"], []),
    ("DEPT-WH", "POS-KEEPER", ["dashboard", "incoming", "ncr", "trace", "material",
                               "customer", "workshop"], []),
    ("DEPT-EQ", "POS-EQ-MGR", ["dashboard", "screen", "equipment", "station", "workshop",
                               "trace"], ["equipment"]),
    ("DEPT-EQ", "POS-FIXER", ["dashboard", "screen", "equipment", "station", "workshop",
                              "trace"], []),
    ("DEPT-TC", "POS-TECH", ["dashboard", "qcstandard", "trace", "report", "material",
                             "station", "equipment"], ["qcstandard"]),
    (None, "POS-OP", ["dashboard", "prodlot"], []),
    (None, "POS-QC", _QC_LINE, []),
]

# 演示账号 → (部门code, 职务code)
_USER_DUTY = {
    "admin": ("DEPT-IT", "POS-ADMIN"), "boss": ("DEPT-GM", "POS-GM"),
    "qm": ("DEPT-QC", "POS-MGR-QC"), "qc": ("DEPT-QC", "POS-QC"),
    "qc2": ("DEPT-QC", "POS-QC"), "sampler": ("DEPT-QC", "POS-SAMPLER"),
    "prodlead": ("DEPT-PR", "POS-PL"), "prodlead2": ("DEPT-PR", "POS-PL"),
    "buyer": ("DEPT-PU", "POS-BUYER"), "store": ("DEPT-WH", "POS-KEEPER"),
}


def seed_duties(db):
    """部门/职务/岗位职责（幂等）：老库增量也补齐，并把演示账号挂到岗位上"""
    db.flush()
    n_dep = n_pos = n_duty = n_user = 0
    for code, name, seq in _DEPTS:
        if not db.query(Department).filter(Department.code == code).first():
            db.add(Department(code=code, name=name, seq=seq)); n_dep += 1
    for code, name, seq in _POSITIONS:
        if not db.query(Position).filter(Position.code == code).first():
            db.add(Position(code=code, name=name, seq=seq)); n_pos += 1
    db.flush()
    dep_map = {d.code: d.id for d in db.query(Department).all()}
    pos_map = {p.code: p.id for p in db.query(Position).all()}
    dep_name = {c: n for c, n, _ in _DEPTS}
    for dep_code, pos_code, pages, mgs in _DUTIES:
        did = dep_map.get(dep_code) if dep_code else None
        pid = pos_map.get(pos_code)
        if not pid:
            continue
        q = db.query(DutyTemplate).filter(DutyTemplate.position_id == pid,
                                          DutyTemplate.enabled == True)
        q = q.filter(DutyTemplate.dept_id == did) if did else q.filter(DutyTemplate.dept_id.is_(None))
        if q.first():
            continue
        db.add(DutyTemplate(dept_id=did, position_id=pid,
                            view_pages=json.dumps(pages, ensure_ascii=False),
                            manage_modules=json.dumps(mgs, ensure_ascii=False),
                            remark="系统内置起步模板，可自行修改", updated_by="system"))
        n_duty += 1
    db.flush()
    for uname, (dc, pc) in _USER_DUTY.items():
        u = db.query(User).filter(User.username == uname).first()
        if u and not u.position_id:
            u.dept_id = dep_map.get(dc)
            u.position_id = pos_map.get(pc)
            if dc in dep_name:
                u.department = dep_name[dc]
            n_user += 1
    db.flush()
    print(f"✔ 岗位职责演示数据: 部门 +{n_dep} / 职务 +{n_pos} / 职责模板 +{n_duty} / 账号挂岗 {n_user}")



def seed_complaints(db):
    """演示客诉：已关闭/调查中/待受理各一条（幂等）"""
    db.flush()
    if db.query(Complaint).count() > 0:
        return
    coas = db.query(Coa).order_by(Coa.id).all()
    if not coas:
        return
    cus = {c.code: c for c in db.query(Customer).all()}
    rows = []
    lot1 = db.get(ProductionLot, coas[0].prod_id)
    lot2 = db.get(ProductionLot, coas[-1].prod_id) if len(coas) > 1 else lot1
    # ① 已关闭：完整 8D 式闭环
    rows.append(dict(
        complaint_no="CS-%d-001" % datetime.now().year,
        customer_id=cus.get("CUS-001").id if cus.get("CUS-001") else None,
        lot_no=lot1.lot_no if lot1 else "", coa_no=coas[0].coa_no,
        claim_type="质量异议", severity=2,
        title="客户反馈磁性异物接近上限，要求提供原因说明",
        content="客户进厂复检发现我方第 3 批产品磁性异物 96 ppb（标准 ≤100 ppb），虽合格但接近上限，要求说明并提交改善措施。",
        root_cause="除磁机磁棒使用周期超过规定（超过 200 批未更换），导致除磁效率下降；同时该批原料硅藻土批次磁性异物偏高。",
        action="1) 立即更换除磁机磁棒并在除磁工序增加磁棒点检记录；2) 将该批原料供应商纳入加严检验（每批必检磁性异物）；3) 修订除磁工序自检项，班组长每班记录磁棒状态。",
        reply="已提交 8D 报告，说明原因与三项改善措施，并附该批 COA 与整改证据。客户确认接受。",
        status=3, created_by="qc", handled_by="qm", closed_by="qm"))
    # ② 调查中
    if lot2:
        rows.append(dict(
            complaint_no="CS-%d-002" % datetime.now().year,
            customer_id=cus.get("CUS-002").id if cus.get("CUS-002") else None,
            lot_no=lot2.lot_no, coa_no=coas[-1].coa_no if coas else "",
            claim_type="包装标识", severity=1,
            title="包装袋批次标识与送货单不一致",
            content="客户反映到货 5 袋中有 1 袋批次标签打印模糊，扫码无法识别，要求补发清晰标签。",
            root_cause="", action="", reply="", status=1, created_by="qc", handled_by="qm"))
    # ③ 待受理
    rows.append(dict(
        complaint_no="CS-%d-003" % datetime.now().year,
        customer_id=cus.get("CUS-002").id if cus.get("CUS-002") else None,
        lot_no="", coa_no="", claim_type="其他", severity=1,
        title="询问下一批交货时间与检测项目",
        content="客户询问下周交付批次的检测项目是否包含氯离子，需质量部答复。",
        root_cause="", action="", reply="", status=0, created_by="buyer"))
    for r in rows:
        kw = dict(r)
        st = kw.pop("status")
        c = Complaint(**kw)
        c.status = st
        if st >= 2:
            c.handled_at = datetime.now()
        if st == 3:
            c.closed_at = datetime.now()
        db.add(c)
    db.flush()
    db.commit()          # 必须提交：session 关闭时未提交的数据会回滚
    print(f"✔ 客诉演示数据: {len(rows)} 条（已关闭1 / 调查中1 / 待受理1）")


def seed():
    ensure_schema()
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            # 已有库：增量补种（标准库 + 质检员多工序 + 来料演示）
            seed_duties(db)
            seed_standards(db)
            seed_check_by(db)
            seed_user_stations(db)
            seed_incoming(db)
            seed_production(db)
            seed_history(db)
            seed_complaints(db)
            print("DB 已有基础数据，完成增量补种")
            return

        # ═══ 车间（磷酸铁产线：单工厂多车间）═══
        ws = [
            Workshop(code="WS-01", name="原料预处理车间", remark="原料溶解/配液"),
            Workshop(code="WS-02", name="合成反应车间", remark="磷酸铁合成主反应"),
            Workshop(code="WS-03", name="洗涤干燥车间", remark="压滤洗涤+干燥"),
            Workshop(code="WS-04", name="成品包装车间", remark="筛分/除磁/包装"),
            Workshop(code="WS-05", name="公用工程车间", remark="水/气/汽供应"),
        ]
        db.add_all(ws); db.flush()

        # ═══ 工序（挂在车间下；next_station 串主链顺序，供父批约束/追溯用）═══
        st_defs = [
            # (code,name,ws序号)
            ("ST01", "溶解配液", 0), ("ST02", "过滤净化", 0),
            ("ST03", "合成反应", 1), ("ST04", "氧化陈化", 1),
            ("ST05", "压滤洗涤", 2), ("ST06", "干燥脱水", 2),
            ("ST07", "粉碎筛分", 3), ("ST08", "除磁包装", 3),
            ("ST09", "水处理", 4), ("ST10", "供汽供电", 4),
        ]
        # 主工艺链（第4步用）：溶解(ST01)→合成(ST03)→压滤(ST05)→干燥(ST06)→包装成品(ST08)
        # 过滤/氧化/粉碎 为可选辅助工序，不强制串主链
        link = {1: 3, 3: 5, 5: 6, 6: 8}
        stations = []
        for code, name, wix in st_defs:
            stations.append(Station(code=code, name=name, workshop_id=ws[wix].id, seq=wix * 10))
        for i, s in enumerate(stations, start=1):
            s.next_station_id = link.get(i)
        db.add_all(stations); db.flush()
        st_map = {s.code: s.id for s in stations}

        # ═══ 设备（挂工序）═══
        eq_defs = [
            ("EQ-001", "1#溶解釜", "ST01", "反应釜"),
            ("EQ-002", "2#溶解釜", "ST01", "反应釜"),
            ("EQ-003", "板框过滤机", "ST02", "过滤设备"),
            ("EQ-004", "1#合成釜", "ST03", "反应釜"),
            ("EQ-005", "2#合成釜", "ST03", "反应釜"),
            ("EQ-006", "氧化釜", "ST04", "反应釜"),
            ("EQ-007", "1#压滤机", "ST05", "压滤机"),
            ("EQ-008", "2#压滤机", "ST05", "压滤机"),
            ("EQ-009", "喷雾干燥塔", "ST06", "干燥设备"),
            ("EQ-010", "闪蒸干燥线", "ST06", "干燥设备"),
            ("EQ-011", "振动筛", "ST07", "筛分设备"),
            ("EQ-012", "除磁机", "ST08", "除磁设备"),
            ("EQ-013", "自动包装线", "ST08", "包装设备"),
        ]
        for code, name, stc, cat in eq_defs:
            db.add(Equipment(code=code, name=name, station_id=st_map[stc], category=cat))

        # ═══ 班组 ═══
        db.add_all([
            Team(name="甲班", leader="刘建国", remark="白班"),
            Team(name="乙班", leader="陈志强", remark="中班"),
            Team(name="丙班", leader="王海军", remark="夜班"),
        ])

        # ═══ 物料（用户提供的真实清单：磷酸铁产线原料/辅料）═══
        mat_defs = [
            # code, 名称, 类型, 规格, 单位
            ("RAW-001", "七水硫酸亚铁", "原料", "FeSO4·7H2O ≥98%", "t"),
            ("RAW-002", "磷酸一铵", "原料", "MAP 工业级 含氮≥11%", "t"),
            ("RAW-003", "85%磷酸", "原料", "H3PO4 含量85%", "t"),
            ("RAW-004", "双氧水", "原料", "H2O2 含量27.5%", "t"),
            ("AUX-001", "木质纤维素", "辅料", "工业级", "t"),
            ("AUX-002", "硅藻土", "辅料", "工业级 食品级过滤助剂", "t"),
            ("AUX-003", "硫酸", "辅料", "H2SO4 ≥98%", "t"),
            # 第4步新增：成品（末道除磁包装产出）
            ("FG-001", "电池级磷酸铁", "成品", "FePO4 电池级 D50 1~3um", "t"),
            ("FG-002", "工业级磷酸铁", "成品", "FePO4 工业级", "t"),
        ]
        for code, name, mtype, spec, unit in mat_defs:
            db.add(Material(code=code, name=name, material_type=mtype, spec=spec, unit=unit))

        # ═══ 供应商/客户 ═══
        db.add_all([
            Supplier(code="SUP-001", name="贵州XX矿业有限公司", contact="张经理", phone="0851-8888xxxx", remark="七水硫酸亚铁主供"),
            Supplier(code="SUP-002", name="湖北XX化工有限公司", contact="李经理", phone="0717-6666xxxx", remark="磷酸一铵/磷酸主供"),
            Supplier(code="SUP-003", name="云南XX过氧化物有限公司", contact="王经理", phone="0874-5555xxxx", remark="双氧水"),
            Supplier(code="SUP-004", name="四川XX助滤材料厂", contact="赵经理", phone="0831-4444xxxx", remark="木质纤维素/硅藻土"),
        ])
        db.add_all([
            Customer(code="CUS-001", name="XX新能源科技有限公司", contact="孙工", phone="0755-3333xxxx", remark="电池级磷酸铁"),
            Customer(code="CUS-002", name="XX电池材料有限公司", contact="周工", phone="0512-2222xxxx", remark="工业级磷酸铁"),
        ])

        # ═══ 账号（10 个演示账号，密码统一 123456，对应角色矩阵）═══
        acc_defs = [
            # (username, 姓名, 部门, 角色, 工序, 说明)
            ("admin", "系统管理员", "信息部", "admin", None),
            ("boss", "王总", "总经办", "boss", None),
            ("qm", "张经理", "质量部", "qm", None),
            ("qc", "李检验", "质量部", "qc", "ST03"),
            ("sampler", "赵取样", "质量部", "sampler", "ST01"),
            ("prodlead", "刘班长", "合成车间", "prodlead", "ST03"),
            ("buyer", "陈采购", "采购部", "buyer", None),
            ("store", "周仓管", "仓储部", "store", None),
            ("qc2", "王化验", "质量部", "qc", "ST06"),
            ("prodlead2", "孙班长", "原料车间", "prodlead", "ST01"),
        ]
        for uname, rname, dept, role, stc in acc_defs:
            salt, h = _hash("123456")
            db.add(User(username=uname, password_salt=salt, password_hash=h,
                        real_name=rname, department=dept, role_key=role,
                        station_id=st_map.get(stc) if stc else None))

        # ═══ 多工序绑定演示：质检员负责多道工序（用户要求）═══
        seed_user_stations(db)

        db.commit()
        print("✔ 建库完成: 车间%d 工序%d 设备%d 班组3 物料%d 供应商4 客户2 账号%d"
              % (len(ws), len(stations), len(eq_defs), len(mat_defs), len(acc_defs)))
        print("  演示账号: admin/qm/qc/sampler/prodlead/buyer/store/qc2/prodlead2/boss, 密码均 123456")
        print("  数据文件: qms.db")

        # ═══ 分权责：部门/职务/岗位职责（并把演示账号挂到岗位上）═══
        seed_duties(db)
        # ═══ 第2步：检验标准库演示数据（按真实物料 × 检验类型）═══
        seed_standards(db)
        seed_check_by(db)
        # ═══ 第3步：来料检验闭环演示数据 ═══
        seed_incoming(db)
        # ═══ 第4步：生产批次/成品检验演示数据 ═══
        seed_production(db)
        # ═══ 第5步：历史数据(报表/SPC/大屏) ═══
        seed_history(db)
        # ═══ 第三批：客诉演示数据 ═══
        seed_complaints(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
