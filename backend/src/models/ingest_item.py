"""来稿回执明细：批次内每一条岗位回执的核对结果（原始回执不丢失）。"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class IngestItem(Base):
    __tablename__ = "ingest_items"
    __table_args__ = (
        UniqueConstraint("batch_id", "seq", name="uq_ingest_item_batch_seq"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("ingest_batches.id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer, default=0)

    external_id: Mapped[str] = mapped_column(String(60), index=True)
    outcome: Mapped[str] = mapped_column(String(16), default="inserted", index=True)
    post_id: Mapped[int | None] = mapped_column(
        ForeignKey("posts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_item: Mapped[dict] = mapped_column("raw_payload", JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
