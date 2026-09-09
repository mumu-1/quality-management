# -*- coding: utf-8 -*-
"""第1步：建库 + 全套演示数据（物料用用户提供的真实清单）"""
import hashlib, os, secrets, sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import Base, engine, SessionLocal, ensure_schema
from models import (User, Material, Supplier, Customer, Workshop, Station,
                    Team, Equipment, QcStandard, QcStandardItem, UserStation,
                    IncomingLot, Sample, TestRecord, TestItem, Ncr)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _hash(pwd, salt=None):
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pwd.encode("utf-8"), salt.encode("utf-8"), 100_000).hex()
    return salt, h


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


def seed():
    ensure_schema()
    db = SessionLocal()
    try:
        if db.query(User).count() > 0:
            # 已有库：增量补种（标准库 + 质检员多工序 + 来料演示）
            seed_standards(db)
            seed_user_stations(db)
            seed_incoming(db)
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

        # ═══ 工序（挂在车间下；next_station 串顺序，供后续追溯）═══
        st_defs = [
            # (code,name,ws序号,next)
            ("ST01", "溶解配液", 0, 2), ("ST02", "过滤净化", 0, None),
            ("ST03", "合成反应", 1, 4), ("ST04", "氧化陈化", 1, None),
            ("ST05", "压滤洗涤", 2, 6), ("ST06", "干燥脱水", 2, None),
            ("ST07", "粉碎筛分", 3, 8), ("ST08", "除磁包装", 3, None),
            ("ST09", "水处理", 4, None), ("ST10", "供汽供电", 4, None),
        ]
        # 上一步: 溶解→过滤→合成→氧化→压滤→干燥→粉碎→包装
        link = {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8}
        stations = []
        for code, name, wix, _nxt in st_defs:
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

        # ═══ 第2步：检验标准库演示数据（按真实物料 × 检验类型）═══
        seed_standards(db)
        # ═══ 第3步：来料检验闭环演示数据 ═══
        seed_incoming(db)
    finally:
        db.close()


if __name__ == "__main__":
    seed()
