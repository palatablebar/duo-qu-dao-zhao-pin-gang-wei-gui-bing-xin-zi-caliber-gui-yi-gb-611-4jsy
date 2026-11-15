"""来稿批次 API 端到端测试：鉴权、RBAC、计数口径与发布清单导出。"""
from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.config import database as db_module
from src.models import Base
from src.models.channel import Channel
from src.services.auth_service import ensure_user


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient

    from src.config.settings import settings

    # 关闭启动 seed，测试自己准备数据
    settings.seed_demo = False

    # StaticPool + check_same_thread=False：让应用各会话共用同一条内存连接
    engine = create_engine(
        "sqlite:///:memory:",
        future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    original_engine = db_module.engine
    original_session = db_module.SessionLocal
    db_module.engine = engine
    db_module.SessionLocal = factory

    with factory() as db:
        db.add(Channel(code="boss", name="BOSS 直聘", kind="api", crawl_cron="0 7 * * *", enabled=True))
        org = "云雀招聘聚合 · 岗位运营组"
        ensure_user(db, "admin@jobmerge.io", "Demo@2026", "林可", "admin", org)
        ensure_user(db, "analyst@jobmerge.io", "Demo@2026", "周棠", "analyst", org)
        ensure_user(db, "viewer@jobmerge.io", "Demo@2026", "陈默", "viewer", org)
        db.commit()

    from src.main import app

    with TestClient(app) as test_client:
        yield test_client

    db_module.engine = original_engine
    db_module.SessionLocal = original_session
    engine.dispose()


def _token(client, email: str) -> str:
    resp = client.post("/api/auth/login", json={"email": email, "password": "Demo@2026"})
    assert resp.status_code == 200
    return resp.json()["access_token"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _batch_payload(batch_no: str = "B-001", source_status: str = "success") -> dict:
    return {
        "channel_code": "boss",
        "batch_no": batch_no,
        "source_status": source_status,
        "expected_external_ids": ["j1", "j2", "j3"],
        "posts": [
            {"external_id": "j1", "company_raw": "甲公司", "title_raw": "Java 开发", "city": "北京", "salary_raw": "10-15K"},
            {"external_id": "j2", "company_raw": "乙公司", "title_raw": "前端开发", "city": "上海", "salary_raw": "8-12K"},
        ],
    }


def test_viewer_cannot_receive_batch_403(client):
    token = _token(client, "viewer@jobmerge.io")
    resp = client.post("/api/ingest/batches", json=_batch_payload(), headers=_auth(token))
    assert resp.status_code == 403
    assert resp.json()["code"] == "FORBIDDEN"


def test_analyst_receives_partial_batch_and_ledger_matches(client):
    token = _token(client, "analyst@jobmerge.io")
    resp = client.post(
        "/api/ingest/batches",
        json=_batch_payload(source_status="partial"),
        headers=_auth(token),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "partial"
    assert body["expected_count"] == 3
    assert body["arrived_count"] == 2
    assert body["missing_count"] == 1
    assert set(body["missing_external_ids"]) == {"j3"}
    assert body["inserted_count"] == 2
    assert body["received_at"] is not None

    ledger = client.get("/api/ingest/ledger", headers=_auth(token)).json()
    row = next(item for item in ledger["items"] if item["channel_code"] == "boss")
    assert row["status"] == "partial"
    assert row["release_state"] == "carried"
    assert row["missing_count"] == 1
    assert ledger["totals"]["publish_complete"] is False

    # 批次列表与详情
    listing = client.get("/api/ingest/batches", headers=_auth(token)).json()
    assert listing["total"] == 1
    detail = client.get(f"/api/ingest/batches/{body['id']}", headers=_auth(token)).json()
    assert len(detail["items"]) == 2
    assert {item["outcome"] for item in detail["items"]} == {"inserted"}


def test_retry_same_batch_is_idempotent_over_http(client):
    token = _token(client, "admin@jobmerge.io")
    first = client.post("/api/ingest/batches", json=_batch_payload(), headers=_auth(token)).json()
    retried = client.post("/api/ingest/batches", json=_batch_payload(), headers=_auth(token)).json()
    assert retried["id"] == first["id"]
    assert retried["retries"] == 1
    # 重试不新增岗位
    posts = client.get("/api/posts?limit=200", headers=_auth(token)).json()
    assert posts["total"] == 2
    assert retried["duplicate_count"] == 2
    assert retried["arrived_count"] == 2


def test_merge_run_reflects_incomplete_edition_and_export(client):
    token = _token(client, "admin@jobmerge.io")
    # 先有一版完整旧稿（j0），再来一批缺量 → j0 变沿用，相关簇暂缓
    client.post(
        "/api/ingest/batches",
        json={
            "channel_code": "boss",
            "batch_no": "B-000",
            "expected_external_ids": ["j0"],
            "posts": [
                {"external_id": "j0", "company_raw": "旧稿公司", "title_raw": "运维工程师", "city": "广州", "salary_raw": "9-14K"},
            ],
        },
        headers=_auth(token),
    )
    client.post("/api/ingest/batches", json=_batch_payload(source_status="timeout"), headers=_auth(token))
    run = client.post("/api/runs", json={"threshold": 0.82}, headers=_auth(token)).json()
    assert run["publish_complete"] is False
    assert run["withheld_clusters"] >= 1

    # 完整发布清单只含可发布簇
    full = client.get("/api/exports/clusters.csv", headers=_auth(token))
    complete = client.get("/api/exports/clusters.csv?complete_only=true", headers=_auth(token))
    assert full.status_code == 200 and complete.status_code == 200
    full_rows = [line for line in full.text.splitlines() if line][1:]
    complete_rows = [line for line in complete.text.splitlines() if line][1:]
    assert len(full_rows) == run["clusters_out"]
    assert len(complete_rows) == run["releasable_clusters"]

    # 来稿台账导出包含应有/实到/缺量
    batches_csv = client.get("/api/exports/ingest-batches.csv", headers=_auth(token))
    assert batches_csv.status_code == 200
    assert "B-001" in batches_csv.text and "本批缺量" in batches_csv.text


def test_validation_error_when_no_expected(client):
    token = _token(client, "admin@jobmerge.io")
    bad = {"channel_code": "boss", "batch_no": "X1", "posts": []}
    resp = client.post("/api/ingest/batches", json=bad, headers=_auth(token))
    assert resp.status_code == 422


def test_unknown_channel_returns_404(client):
    token = _token(client, "admin@jobmerge.io")
    payload = {
        "channel_code": "ghost",
        "batch_no": "X1",
        "expected_count": 1,
        "posts": [{"external_id": "g1", "company_raw": "甲", "title_raw": "乙", "city": "北京", "salary_raw": "10K"}],
    }
    resp = client.post("/api/ingest/batches", json=payload, headers=_auth(token))
    assert resp.status_code == 404


def test_unauthenticated_request_401(client):
    resp = client.get("/api/ingest/ledger")
    assert resp.status_code == 401
