"""测试夹具：用 SQLite 建内存库，替换全局 engine/SessionLocal。

业务代码全部走 SQLAlchemy 2.0 通用类型（JSON/Boolean/DateTime），
Postgres 专有语法没有出现在 ORM 路径上，因此可用 SQLite 做服务层测试。
"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import database as db_module
from src.models import Base


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    original_engine = db_module.engine
    original_session = db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = factory
    session = factory()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(engine)
        engine.dispose()
        db_module.engine = original_engine
        db_module.SessionLocal = original_session


@pytest.fixture()
def channel_factory(db):
    from src.models.channel import Channel

    def _make(code: str = "boss", name: str | None = None):
        channel = Channel(
            code=code,
            name=name or f"渠道-{code}",
            kind="api",
            crawl_cron="0 7 * * *",
            enabled=True,
        )
        db.add(channel)
        db.flush()
        return channel

    return _make


def post_payload(external_id: str, **overrides) -> dict:
    base = {
        "external_id": external_id,
        "company_raw": "云雀数智科技有限公司",
        "title_raw": "Java开发工程师",
        "city": "北京",
        "salary_raw": "10-15K",
        "degree": "本科",
        "experience": "3-5年",
    }
    base.update(overrides)
    return base
