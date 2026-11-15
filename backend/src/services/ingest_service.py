"""来稿批次服务：回执接收、应有/实到/重复核对、旧稿沿用与隔离、渠道发布台账。

核心口径（不允许旧岗位冒充新稿）：

* 每个批次记录应有数 expected_count、实到数 arrived_count、重复数 duplicate_count、
  无效数 invalid_count、缺量 missing_count 与接收时间 received_at；
* 同 (channel_id, batch_no) 的重复提交视为「重试同一批」，幂等处理，
  已入库岗位靠 (channel_id, external_id) 自然键识别，绝不重复新增；
* 本批缺量（partial）时，该渠道历史岗位标记为 carried（旧稿沿用），不标成新稿；
* 本批失败（failed）时，该渠道岗位标记为 isolated（隔离），不参与归并与发布；
* 原始回执整体存入 ingest_batches.raw_receipt，逐条回执存入 ingest_items，不丢数据。
"""
from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.constants.enums import (
    BATCH_STATUS_LABELS,
    BatchStatus,
    ITEM_OUTCOME_LABELS,
    ItemOutcome,
    POST_INGEST_LABELS,
    PostIngestState,
    RELEASE_STATE_LABELS,
    ReleaseState,
)
from src.models.channel import Channel
from src.models.ingest_batch import IngestBatch
from src.models.ingest_item import IngestItem
from src.models.post import Post
from src.services.normalize_service import enrich_post_fields
from src.utils.errors import AppError, not_found
from src.utils.timeutil import iso_utc, utcnow

REQUIRED_POST_FIELDS = ("external_id", "company_raw", "title_raw", "city", "salary_raw")
VALID_OUTCOMES = (ItemOutcome.INSERTED.value, ItemOutcome.DUPLICATE.value)


# ---------------------------------------------------------------- 读取与序列化
def get_channel_or_404(db: Session, channel_code: str) -> Channel:
    channel = db.scalar(select(Channel).where(Channel.code == channel_code))
    if channel is None:
        raise not_found("channel", channel_code)
    return channel


def serialize_batch(batch: IngestBatch, channel: Channel | None, items: list[IngestItem] | None = None) -> dict:
    payload = {
        "id": batch.id,
        "channel_id": batch.channel_id,
        "channel_code": channel.code if channel else None,
        "channel_name": channel.name if channel else None,
        "batch_no": batch.batch_no,
        "status": batch.status,
        "status_label": BATCH_STATUS_LABELS.get(batch.status, batch.status),
        "source_status": batch.source_status,
        "expected_count": batch.expected_count,
        "arrived_count": batch.arrived_count,
        "inserted_count": batch.inserted_count,
        "duplicate_count": batch.duplicate_count,
        "invalid_count": batch.invalid_count,
        "missing_count": batch.missing_count,
        "carried_count": batch.carried_count,
        "isolated_count": batch.isolated_count,
        "expected_external_ids": batch.expected_external_ids or [],
        "missing_external_ids": batch.missing_external_ids or [],
        "note": batch.note,
        "retries": batch.retries,
        "reconciled": batch.reconciled,
        "received_at": iso_utc(batch.received_at),
        "created_at": iso_utc(batch.created_at),
        "updated_at": iso_utc(batch.updated_at),
    }
    if items is not None:
        payload["items"] = [
            {
                "seq": item.seq,
                "external_id": item.external_id,
                "outcome": item.outcome,
                "outcome_label": ITEM_OUTCOME_LABELS.get(item.outcome, item.outcome),
                "post_id": item.post_id,
                "reason": item.reason,
            }
            for item in items
        ]
    return payload


def list_batches(
    db: Session,
    *,
    channel_id: int | None = None,
    status: str | None = None,
    limit: int = 30,
    offset: int = 0,
) -> dict:
    stmt = select(IngestBatch)
    count_stmt = select(func.count()).select_from(IngestBatch)
    if channel_id:
        stmt = stmt.where(IngestBatch.channel_id == channel_id)
        count_stmt = count_stmt.where(IngestBatch.channel_id == channel_id)
    if status:
        stmt = stmt.where(IngestBatch.status == status)
        count_stmt = count_stmt.where(IngestBatch.status == status)
    total = db.scalar(count_stmt) or 0
    rows = db.scalars(
        stmt.order_by(IngestBatch.received_at.desc(), IngestBatch.id.desc()).limit(limit).offset(offset)
    ).all()
    channels = {item.id: item for item in db.scalars(select(Channel)).all()}
    return {
        "items": [serialize_batch(row, channels.get(row.channel_id)) for row in rows],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def get_batch(db: Session, batch_id: int) -> tuple[IngestBatch, Channel, list[IngestItem]]:
    batch = db.get(IngestBatch, batch_id)
    if batch is None:
        raise AppError("NOT_FOUND", 404, {"entity": "ingest_batch", "id": batch_id})
    channel = db.get(Channel, batch.channel_id)
    items = db.scalars(
        select(IngestItem).where(IngestItem.batch_id == batch.id).order_by(IngestItem.seq, IngestItem.id)
    ).all()
    return batch, channel, items


def latest_batch_map(db: Session) -> dict[int, IngestBatch]:
    """每个渠道最新一个批次（按接收时间）。"""
    rows = db.scalars(select(IngestBatch).order_by(IngestBatch.channel_id, IngestBatch.received_at, IngestBatch.id)).all()
    return {row.channel_id: row for row in rows}


# ---------------------------------------------------------------- 接收与核对
def _classify(source_status: str, expected: int, arrived: int, missing: int) -> str:
    if source_status == BatchStatus.FAILED.value or arrived == 0:
        return BatchStatus.FAILED.value
    if missing > 0 or arrived < expected or source_status in ("partial", "timeout"):
        return BatchStatus.PARTIAL.value
    return BatchStatus.COMPLETE.value


def _reconcile_channel_posts(
    db: Session,
    *,
    channel_id: int,
    batch: IngestBatch,
    arrived_external_ids: set[str],
) -> tuple[int, int]:
    """按本批结论重写该渠道全部岗位的 ingest_state，返回 (沿用数, 隔离数)。

    本批有效到达的外部ID 含新入库与自然键已存在（失败后重试补到）的回执。
    """
    posts = db.scalars(select(Post).where(Post.channel_id == channel_id)).all()
    carried = 0
    isolated = 0
    for post in posts:
        in_this_batch = post.ingest_batch_id == batch.id or post.external_id in arrived_external_ids
        if batch.status == BatchStatus.FAILED.value:
            # 失败批次：该渠道岗位全部隔离，不参与归并与发布
            post.ingest_state = PostIngestState.ISOLATED.value
            isolated += 1
        elif in_this_batch:
            # 本批实到（含失败后重试补到）的有效岗位始终是新稿；缺量只让未到旧稿沿用
            post.ingest_state = PostIngestState.FRESH.value
        else:
            # complete：本批之外的历史岗位转为沿用；partial：历史岗位沿用补位
            post.ingest_state = PostIngestState.CARRIED.value
            carried += 1
    return carried, isolated


def receive_batch(db: Session, payload: dict) -> IngestBatch:
    channel = get_channel_or_404(db, payload["channel_code"])
    batch_no = payload["batch_no"]
    now = utcnow()

    existing = db.scalar(
        select(IngestBatch).where(
            IngestBatch.channel_id == channel.id, IngestBatch.batch_no == batch_no
        )
    )
    is_retry = existing is not None
    if is_retry:
        # 重试同一批：清掉上一轮逐条回执重算，保留首次接收时间；岗位靠自然键不重复新增
        batch = existing
        for old_item in db.scalars(select(IngestItem).where(IngestItem.batch_id == batch.id)).all():
            db.delete(old_item)
        db.flush()  # 先落库删除，避免 (batch_id, seq) 唯一约束与新一轮回执冲突
        batch.retries = (batch.retries or 0) + 1
    else:
        batch = IngestBatch(
            channel_id=channel.id,
            batch_no=batch_no,
            received_at=now,
            retries=0,
        )
        db.add(batch)
        db.flush()

    expected_ids: list[str] = payload.get("expected_external_ids") or []
    expected_count = payload.get("expected_count")
    if not expected_ids:
        expected_count = expected_count if expected_count is not None else len(payload["posts"])
    else:
        expected_count = len(expected_ids)
    expected_set = set(expected_ids)

    seen: set[str] = set()
    inserted = 0
    duplicate = 0
    invalid = 0
    arrived_ids: set[str] = set()
    items: list[IngestItem] = []

    for seq, raw in enumerate(payload["posts"], start=1):
        external_id = (raw.get("external_id") or "").strip()
        missing_fields = [name for name in REQUIRED_POST_FIELDS if not str(raw.get(name) or "").strip()]
        if missing_fields:
            invalid += 1
            items.append(
                IngestItem(
                    batch_id=batch.id,
                    seq=seq,
                    external_id=external_id or f"#row-{seq}",
                    outcome=ItemOutcome.INVALID.value,
                    reason=f"必填字段缺失：{'、'.join(missing_fields)}",
                    raw_item=raw,
                )
            )
            continue
        if external_id in seen:
            duplicate += 1
            items.append(
                IngestItem(
                    batch_id=batch.id,
                    seq=seq,
                    external_id=external_id,
                    outcome=ItemOutcome.DUPLICATE.value,
                    reason="同一批次内重复回执",
                    raw_item=raw,
                )
            )
            continue
        seen.add(external_id)

        existing_post = db.scalar(
            select(Post).where(Post.channel_id == channel.id, Post.external_id == external_id)
        )
        if existing_post is not None:
            # 自然键已存在：重试或重复来稿，不新增岗位
            duplicate += 1
            items.append(
                IngestItem(
                    batch_id=batch.id,
                    seq=seq,
                    external_id=external_id,
                    outcome=ItemOutcome.DUPLICATE.value,
                    post_id=existing_post.id,
                    reason="岗位自然键（渠道+外部ID）已存在，未重复新增",
                    raw_item=raw,
                )
            )
            arrived_ids.add(external_id)
            continue

        derived = enrich_post_fields(
            company_raw=raw["company_raw"],
            title_raw=raw["title_raw"],
            city=raw["city"],
            salary_raw=raw["salary_raw"],
            degree=raw.get("degree") or "不限",
            experience=raw.get("experience") or "不限",
        )
        post = Post(
            channel_id=channel.id,
            external_id=external_id,
            company_raw=raw["company_raw"],
            company_norm=derived["company_norm"],
            title_raw=raw["title_raw"],
            title_stem=derived["title_stem"],
            city=derived["city_norm"],
            salary_raw=raw["salary_raw"],
            salary_status=derived["salary_status"],
            salary_low=derived["salary_low"],
            salary_high=derived["salary_high"],
            months=derived["months"],
            monthly_low=derived["monthly_low"],
            monthly_high=derived["monthly_high"],
            monthly_median=derived["monthly_median"],
            salary_basis=derived["salary_basis"],
            salary_confidence=derived["salary_confidence"],
            degree=derived["degree"],
            experience=derived["experience"],
            published_at=now,
            raw_text=(
                f"{raw['company_raw']} 招聘 {raw['title_raw']}，城市 {raw['city']}，"
                f"薪资 {raw['salary_raw']}，学历 {raw.get('degree') or '不限'}，"
                f"经验 {raw.get('experience') or '不限'}"
            ),
            ingest_batch_id=batch.id,
            ingest_state=PostIngestState.FRESH.value,
        )
        db.add(post)
        db.flush()
        inserted += 1
        arrived_ids.add(external_id)
        items.append(
            IngestItem(
                batch_id=batch.id,
                seq=seq,
                external_id=external_id,
                outcome=ItemOutcome.INSERTED.value,
                post_id=post.id,
                raw_item=raw,
            )
        )

    db.add_all(items)

    # 实到数 = 有效且去重后真实到达的岗位数（批内重复与无效行不算实到）
    arrived_count = len(arrived_ids)
    missing_ids = sorted(expected_set - arrived_ids)
    status = _classify(payload.get("source_status", "success"), expected_count, arrived_count, len(missing_ids))

    batch.source_status = payload.get("source_status", "success")
    batch.status = status
    batch.expected_count = expected_count
    batch.expected_external_ids = expected_ids
    batch.arrived_count = arrived_count
    batch.inserted_count = inserted
    batch.duplicate_count = duplicate
    batch.invalid_count = invalid
    batch.missing_count = len(missing_ids)
    batch.missing_external_ids = missing_ids
    batch.raw_receipt = payload
    batch.note = payload.get("note")
    batch.reconciled = True

    carried, isolated = _reconcile_channel_posts(
        db,
        channel_id=channel.id,
        batch=batch,
        arrived_external_ids=arrived_ids,
    )
    batch.carried_count = carried
    batch.isolated_count = isolated
    db.flush()

    channel.last_crawled_at = batch.received_at
    return batch


# ---------------------------------------------------------------- 渠道发布台账
def _channel_release(channel: Channel, batch: IngestBatch | None) -> dict:
    if batch is None:
        return {
            "release_state": ReleaseState.ISOLATED.value,
            "release_label": RELEASE_STATE_LABELS[ReleaseState.ISOLATED.value],
        }
    if batch.status == BatchStatus.COMPLETE.value:
        state = ReleaseState.RELEASABLE.value
    elif batch.status == BatchStatus.FAILED.value:
        state = ReleaseState.ISOLATED.value
    else:
        state = ReleaseState.CARRIED.value
    return {"release_state": state, "release_label": RELEASE_STATE_LABELS.get(state, state)}


def channel_ledger(db: Session) -> dict:
    """按渠道汇总最新批次口径：收齐/缺量/失败、缺多少、沿用或隔离多少。"""
    channels = db.scalars(select(Channel).order_by(Channel.id)).all()
    latest = latest_batch_map(db)
    post_rows = db.execute(
        select(Post.channel_id, Post.ingest_state, func.count()).group_by(Post.channel_id, Post.ingest_state)
    ).all()
    state_counts: dict[int, dict[str, int]] = {}
    for channel_id, state, count in post_rows:
        state_counts.setdefault(int(channel_id), {})[state] = int(count)

    items = []
    totals = {
        "channels": len(channels),
        "complete": 0,
        "partial": 0,
        "failed": 0,
        "no_batch": 0,
        "expected": 0,
        "arrived": 0,
        "missing": 0,
        "duplicate": 0,
        "carried_posts": 0,
        "isolated_posts": 0,
        "fresh_posts": 0,
        "publish_complete": True,
    }
    for channel in channels:
        batch = latest.get(channel.id)
        counts = state_counts.get(channel.id, {})
        release = _channel_release(channel, batch)
        if batch is None:
            totals["no_batch"] += 1
            totals["publish_complete"] = False
        elif batch.status == BatchStatus.COMPLETE.value:
            totals["complete"] += 1
        elif batch.status == BatchStatus.FAILED.value:
            totals["failed"] += 1
            totals["publish_complete"] = False
        else:
            totals["partial"] += 1
            totals["publish_complete"] = False
        if batch:
            totals["expected"] += batch.expected_count
            totals["arrived"] += batch.arrived_count
            totals["missing"] += batch.missing_count
            totals["duplicate"] += batch.duplicate_count
        totals["carried_posts"] += counts.get(PostIngestState.CARRIED.value, 0)
        totals["isolated_posts"] += counts.get(PostIngestState.ISOLATED.value, 0)
        totals["fresh_posts"] += counts.get(PostIngestState.FRESH.value, 0)
        items.append(
            {
                "channel_id": channel.id,
                "channel_code": channel.code,
                "channel_name": channel.name,
                "enabled": channel.enabled,
                "latest_batch_no": batch.batch_no if batch else None,
                "latest_batch_id": batch.id if batch else None,
                "status": batch.status if batch else "no_batch",
                "status_label": BATCH_STATUS_LABELS.get(batch.status, "尚无来稿") if batch else "尚无来稿",
                "expected_count": batch.expected_count if batch else 0,
                "arrived_count": batch.arrived_count if batch else 0,
                "duplicate_count": batch.duplicate_count if batch else 0,
                "invalid_count": batch.invalid_count if batch else 0,
                "missing_count": batch.missing_count if batch else 0,
                "carried_count": batch.carried_count if batch else 0,
                "fresh_count": counts.get(PostIngestState.FRESH.value, 0),
                "isolated_count": counts.get(PostIngestState.ISOLATED.value, 0),
                "missing_external_ids": batch.missing_external_ids if batch else [],
                "received_at": iso_utc(batch.received_at) if batch else None,
                "retries": batch.retries if batch else 0,
                **release,
            }
        )
    return {"items": items, "totals": totals, "state_labels": POST_INGEST_LABELS}
