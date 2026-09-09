# -*- coding: utf-8 -*-
"""第1步数据模型：账号/权限 + 基础资料（物料/供应商/客户/车间/工序/班组/设备）+ 审计日志"""
from datetime import datetime
from sqlalchemy import (Column, Integer, String, Text, Boolean, DateTime,
                        ForeignKey, Float)
from sqlalchemy.orm import relationship
from database import Base


class User(Base):
    __tablename__ = "user"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_salt = Column(String(32), nullable=False)
    password_hash = Column(String(128), nullable=False)
    real_name = Column(String(50), nullable=False)
    department = Column(String(50), default="")        # 部门/车间
    role_key = Column(String(30), nullable=False)      # admin/boss/qm/qc/sampler/prodlead/buyer/store
    station_id = Column(Integer, nullable=True)        # 主绑定工序（兼容旧数据）
    view_pages = Column(Text, nullable=True)           # JSON 数组；NULL=按角色默认模板
    manage_modules = Column(Text, nullable=True)       # JSON 数组；NULL=按角色默认
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class UserStation(Base):
    """用户↔工序 多对多：一人可负责多道工序（质检员管多工序）"""
    __tablename__ = "user_station"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False, index=True)
    station_id = Column(Integer, ForeignKey("station.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.now)


class Material(Base):
    __tablename__ = "material"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)   # 静态编码不带日期, 如 RAW-001
    name = Column(String(100), nullable=False)
    material_type = Column(String(20), nullable=False)       # 原料/辅料/中间品/成品
    spec = Column(String(200), default="")                   # 规格型号/含量要求
    unit = Column(String(20), default="t")                   # 计量单位
    remark = Column(String(300), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class Supplier(Base):
    __tablename__ = "supplier"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    contact = Column(String(50), default="")
    phone = Column(String(50), default="")
    remark = Column(String(300), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class Customer(Base):
    __tablename__ = "customer"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)
    name = Column(String(100), nullable=False)
    contact = Column(String(50), default="")
    phone = Column(String(50), default="")
    remark = Column(String(300), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class Workshop(Base):
    """车间（单工厂多车间）"""
    __tablename__ = "workshop"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)   # WS-01
    name = Column(String(100), nullable=False)
    remark = Column(String(300), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class Station(Base):
    """工序/岗位，挂在车间下；next_station_id 串起工艺顺序（供追溯用）"""
    __tablename__ = "station"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)   # ST01
    name = Column(String(100), nullable=False)
    workshop_id = Column(Integer, ForeignKey("workshop.id"), nullable=False)
    seq = Column(Integer, default=0)                          # 工序序号
    next_station_id = Column(Integer, nullable=True)          # 下一道工序
    remark = Column(String(300), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    workshop = relationship("Workshop")


class Team(Base):
    """班组"""
    __tablename__ = "team"
    id = Column(Integer, primary_key=True)
    name = Column(String(50), unique=True, nullable=False)
    leader = Column(String(50), default="")
    remark = Column(String(300), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)


class Equipment(Base):
    """设备，挂工序（车间经工序带出）"""
    __tablename__ = "equipment"
    id = Column(Integer, primary_key=True)
    code = Column(String(50), unique=True, nullable=False)   # EQ-001
    name = Column(String(100), nullable=False)
    station_id = Column(Integer, ForeignKey("station.id"), nullable=False)
    category = Column(String(50), default="")                # 反应釜/压滤机/干燥设备/泵…
    remark = Column(String(300), default="")
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)
    station = relationship("Station")


class AuthToken(Base):
    """登录令牌（DB 存储，多进程通用；上线可换 JWT）"""
    __tablename__ = "auth_token"
    token = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.now)
    expires_at = Column(DateTime, nullable=False)


class AuditLog(Base):
    """操作留痕：谁在什么时候对什么做了什么"""
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), default="")
    action = Column(String(50), default="")       # create/update/delete/import/login
    target = Column(String(100), default="")      # 如 material:RAW-001
    detail = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.now)


class QcStandard(Base):
    """检验标准头：绑定 物料/工序 × 检验类型（iqc来料/ipqc过程/oqc成品）"""
    __tablename__ = "qc_standard"
    id = Column(Integer, primary_key=True)
    std_no = Column(String(50), unique=True, nullable=False)     # STD-001
    name = Column(String(120), nullable=False)                   # 显示名：如"磷酸一铵-来料检验标准"
    object_type = Column(String(20), nullable=False)             # material 物料 / station 工序
    object_id = Column(Integer, nullable=False)
    check_type = Column(String(20), nullable=False)              # iqc / ipqc / oqc
    sample_qty = Column(Integer, default=1)                      # 取样数量(批)
    remark = Column(String(300), default="")
    status = Column(Integer, default=1)                          # 1启用 0停用
    version = Column(Integer, default=1)                         # 每次保存明细 +1
    updated_by = Column(String(50), default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)


class QcStandardItem(Base):
    """检验标准明细：一个指标一行"""
    __tablename__ = "qc_standard_item"
    id = Column(Integer, primary_key=True)
    standard_id = Column(Integer, ForeignKey("qc_standard.id"), nullable=False, index=True)
    seq = Column(Integer, default=0)              # 顺序
    indicator = Column(String(100), nullable=False)  # 指标名：主含量/水分/pH…
    unit = Column(String(20), default="")         # %
    min_val = Column(Float, nullable=True)        # 下限（可空）
    max_val = Column(Float, nullable=True)        # 上限（可空）
    method = Column(String(200), default="")      # 检验方法/依据
    is_key = Column(Integer, default=0)           # 1=关键指标（客户匹配用）
    enabled = Column(Integer, default=1)


class IncomingLot(Base):
    """来料批次（第3步）：到货登记时生成，批号 YYYYMMDD-RA-SUPXX-NNN"""
    __tablename__ = "incoming_lot"
    id = Column(Integer, primary_key=True)
    lot_no = Column(String(60), unique=True, nullable=False)
    material_id = Column(Integer, ForeignKey("material.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("supplier.id"), nullable=False)
    supplier_lot = Column(String(80), default="")     # 供应商自己批号
    qty = Column(Float, default=0)                    # 到货数量
    unit = Column(String(20), default="t")
    vehicle = Column(String(50), default="")          # 车牌（可选）
    status = Column(Integer, default=0)               # 0待取样 1待检验 2合格放行 3不合格冻结 4让步接收 5拒收/退货
    arrival_by = Column(String(50), default="")
    remark = Column(String(300), default="")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now)
    material = relationship("Material")
    supplier = relationship("Supplier")


class Sample(Base):
    """取样单：一车/一批取样送检"""
    __tablename__ = "sample"
    id = Column(Integer, primary_key=True)
    sample_no = Column(String(60), unique=True, nullable=False)   # SP-YYYYMMDD-NNN
    lot_id = Column(Integer, ForeignKey("incoming_lot.id"), nullable=False, index=True)
    sample_by = Column(String(50), default="")
    sample_qty = Column(String(50), default="")       # 取样量描述
    status = Column(Integer, default=0)               # 0已取样待检 1已检
    created_at = Column(DateTime, default=datetime.now)
    lot = relationship("IncomingLot")


class TestRecord(Base):
    """检验单头：一次检验=一批×一套标准"""
    __tablename__ = "test_record"
    id = Column(Integer, primary_key=True)
    test_no = Column(String(60), unique=True, nullable=False)     # T-YYYYMMDD-NNN
    lot_id = Column(Integer, ForeignKey("incoming_lot.id"), nullable=False, index=True)
    sample_id = Column(Integer, nullable=True)
    std_id = Column(Integer, ForeignKey("qc_standard.id"), nullable=False)  # 用的哪套标准
    check_type = Column(String(10), default="iqc")
    result = Column(Integer, default=0)               # 0待定/检验中 1合格 2不合格
    tested_by = Column(String(50), default="")
    remark = Column(String(300), default="")
    created_at = Column(DateTime, default=datetime.now)
    lot = relationship("IncomingLot")


class TestItem(Base):
    """检验单明细：每个指标一行（快照标准限值，标准后续改了不影响历史）"""
    __tablename__ = "test_item"
    id = Column(Integer, primary_key=True)
    test_id = Column(Integer, ForeignKey("test_record.id"), nullable=False, index=True)
    seq = Column(Integer, default=0)
    indicator = Column(String(100), nullable=False)
    unit = Column(String(20), default="")
    min_val = Column(Float, nullable=True)
    max_val = Column(Float, nullable=True)
    method = Column(String(200), default="")
    is_key = Column(Integer, default=0)
    actual = Column(String(50), nullable=True)        # 实测值（存字符串，允许"目测合格"类）
    pass_flag = Column(Integer, nullable=True)        # 1合格 0不合格 NULL未检
    remark = Column(String(200), default="")


class Ncr(Base):
    """不合格处理单（第3步简化版）：检验不合格自动生成
    status: 0待处理(采购/质量) 1拒收退货(采购处理完) 2让步接收(质量经理批) 3报废(质量经理批)"""
    __tablename__ = "ncr"
    id = Column(Integer, primary_key=True)
    ncr_no = Column(String(60), unique=True, nullable=False)      # NCR-YYYY-NNN
    lot_id = Column(Integer, ForeignKey("incoming_lot.id"), nullable=False, index=True)
    test_id = Column(Integer, nullable=True)
    fail_summary = Column(String(500), default="")    # 不合格项汇总
    disposition = Column(String(20), default="")      # reject/waive/scrap
    status = Column(Integer, default=0)               # 0待处理 1已拒收退货 2已让步接收 3已报废
    created_by = Column(String(50), default="")
    handled_by = Column(String(50), default="")
    handled_at = Column(DateTime, nullable=True)
    remark = Column(String(300), default="")
    created_at = Column(DateTime, default=datetime.now)
    lot = relationship("IncomingLot")
