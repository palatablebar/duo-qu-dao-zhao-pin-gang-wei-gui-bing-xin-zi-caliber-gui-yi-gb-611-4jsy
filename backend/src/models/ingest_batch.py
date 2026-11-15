"""来稿批次：某渠道按采集批次回传一次岗位的回执与核对结果。

一个批次回答四件事：应有多少（expected_count）、实到多少（arrived_count）、
重复多少（duplicate_count）、何时接收（received_at）。批次状态由核对口径决定，
旧岗位是否能沿用、是否要隔离都挂在批次结论上，而不是混进总量。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from src.models import Base
from src.utils.timeutil import utcnow


class IngestBatch(Base):
    __tablename__ = "ingest_batches"
    __table_args__ = (
        UniqueConstraint("channel_id", "batch_no", name="uq_ingest_batch_channel_no"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    batch_no: Mapped[str] = mapped_column(String(60), index=True)

    status: Mapped[str] = mapped_column(String(16), default="partial", index=True)
    source_status: Mapped[str] = mapped_column(String(20), default="partial")
    expected_count: Mapped[int] = mapped_column(Integer, default=0)
    arrived_count: Mapped[int] = mapped_column(Integer, default=0)
    inserted_count: Mapped[int] = mapped_column(Integer, default=0)
    duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    invalid_count: Mapped[int] = mapped_column(Integer, default=0)
    missing_count: Mapped[int] = mapped_column(Integer, default=0)
    carried_count: Mapped[int] = mapped_column(Integer, default=0)
    isolated_count: Mapped[int] = mapped_column(Integer, default=0)

    expected_external_ids: Mapped[list] = mapped_column(JSON, default=list)
    missing_external_ids: Mapped[list] = mapped_column(JSON, default=list)
    raw_receipt: Mapped[dict] = mapped_column(JSON, default=dict)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    reconciled: Mapped[bool] = mapped_column(Boolean, default=False)

    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
