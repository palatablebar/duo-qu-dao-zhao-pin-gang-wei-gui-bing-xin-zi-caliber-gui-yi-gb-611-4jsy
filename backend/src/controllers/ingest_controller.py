"""来稿批次控制器：接收回执写领域审计，读取返回核对结果。"""
from __future__ import annotations

from fastapi import Request
from sqlalchemy.orm import Session

from src.models.channel import Channel
from src.services import audit_service, ingest_service


def receive(db: Session, payload: dict, principal: dict, request: Request) -> dict:
    batch = ingest_service.receive_batch(db, payload)
    audit_service.record(
        db,
        actor_email=principal["email"],
        actor_role=principal["role"],
        action="ingest.receive",
        entity="ingest_batch",
        entity_id=batch.id,
        detail={
            "channel_id": batch.channel_id,
            "batch_no": batch.batch_no,
            "status": batch.status,
            "retry": bool(batch.retries),
            "expected": batch.expected_count,
            "arrived": batch.arrived_count,
            "duplicate": batch.duplicate_count,
            "invalid": batch.invalid_count,
            "missing": batch.missing_count,
        },
        ip=request.client.host if request.client else None,
    )
    db.commit()
    db.refresh(batch)
    channel = db.get(Channel, batch.channel_id)
    _, _, items = ingest_service.get_batch(db, batch.id)
    return ingest_service.serialize_batch(batch, channel, items)


def list_batches(db: Session, **filters) -> dict:
    return ingest_service.list_batches(db, **filters)


def get_batch(db: Session, batch_id: int) -> dict:
    batch, channel, items = ingest_service.get_batch(db, batch_id)
    return ingest_service.serialize_batch(batch, channel, items)


def ledger(db: Session) -> dict:
    return ingest_service.channel_ledger(db)
