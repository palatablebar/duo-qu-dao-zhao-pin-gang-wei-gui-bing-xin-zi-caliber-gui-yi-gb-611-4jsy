from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from src.config.database import get_db
from src.constants.roles import INGEST_WRITE_ROLES, READ_ROLES
from src.controllers import ingest_controller
from src.middlewares.rbac import require_roles
from src.schemas.ingest import IngestIn
from src.utils.pagination import page_params

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.get("/ledger")
def channel_ledger(
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    return ingest_controller.ledger(db)


@router.get("/batches")
def list_batches(
    channel_id: int | None = None,
    status: str | None = None,
    limit: int = Query(30),
    offset: int = Query(0),
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    page = page_params(limit, offset)
    return ingest_controller.list_batches(
        db, channel_id=channel_id, status=status, limit=page.limit, offset=page.offset
    )


@router.get("/batches/{batch_id}")
def get_batch(
    batch_id: int,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*READ_ROLES)),
) -> dict:
    return ingest_controller.get_batch(db, batch_id)


@router.post("/batches", status_code=201)
def receive_batch(
    payload: IngestIn,
    request: Request,
    db: Session = Depends(get_db),
    principal: dict = Depends(require_roles(*INGEST_WRITE_ROLES)),
) -> dict:
    return ingest_controller.receive(db, payload.model_dump(), principal, request)
