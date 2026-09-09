# -*- coding: utf-8 -*-
"""数据库引擎：开发默认 SQLite，上线通过环境变量 QMS_DB_URL 切换 MySQL。
例: set QMS_DB_URL=mysql+pymysql://qms:pass@127.0.0.1:3306/qms
"""
import os
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker, declarative_base

DB_URL = os.environ.get("QMS_DB_URL", "sqlite:///./qms.db")

_engine_kwargs = {}
if DB_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DB_URL, pool_pre_ping=True, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ensure_schema():
    """建表 + 幂等轻量迁移：给已存在的旧库补新列/新表，不丢数据。
    - create_all: 新建缺失的表（含 user_station 多对多表）
    - 旧 user 表补 view_pages/manage_modules 列：SQLite ALTER TABLE 不支持
      IF NOT EXISTS，且 DDL 参与事务——每条 ALTER 用独立事务，失败静默跳过
      （列已存在 / MySQL 场景均无需 ALTER，靠 create_all 保证）
    """
    Base.metadata.create_all(engine)
    insp = inspect(engine)
    if "user" in insp.get_table_names():
        cols = {c["name"] for c in insp.get_columns("user")}
        for col in ("view_pages", "manage_modules"):
            if col not in cols:
                try:
                    with engine.begin() as conn:
                        conn.execute(text(f"ALTER TABLE user ADD COLUMN {col} TEXT"))
                except Exception:
                    pass  # 列已存在等 → 无需处理
