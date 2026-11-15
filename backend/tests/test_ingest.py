"""来稿批次核对与发布口径的端到端服务测试。

覆盖需求：
1. 按批次核对应有/实到/重复/接收时间；
2. 本批缺量时旧岗位标记 carried（沿用），不冒充新稿；
3. 其他渠道可先处理，但不发布成完整版本（publish_complete=False）；
4. 重试同一批不重复新增岗位（自然键幂等）；
5. 页面台账按渠道说明收齐/缺量/沿用/隔离；
6. 发布清单与计数逐项对上；
7. 原始回执与岗位记录不丢失。
"""
from __future__ import annotations

from sqlalchemy import func, select

from src.constants.enums import (
    BatchStatus,
    ItemOutcome,
    PostIngestState,
    ReleaseState,
)
from src.models.cluster import Cluster
from src.models.ingest_batch import IngestBatch
from src.models.ingest_item import IngestItem
from src.models.post import Post
from src.services import export_service, ingest_service, run_service
from tests.conftest import post_payload


def _receive(db, channel_code, batch_no, posts, *, expected=None, expected_ids=None, source_status="success", note=None):
    payload = {
        "channel_code": channel_code,
        "batch_no": batch_no,
        "source_status": source_status,
        "posts": posts,
        "note": note,
    }
    if expected is not None:
        payload["expected_count"] = expected
    if expected_ids is not None:
        payload["expected_external_ids"] = expected_ids
    return ingest_service.receive_batch(db, payload)


# ---------------------------------------------------------------- 1. 核对计数
def test_complete_batch_counts_expected_arrived_duplicate(db, channel_factory):
    channel = channel_factory("boss")
    ids = ["b1", "b2", "b3"]
    batch = _receive(
        db,
        "boss",
        "B-001",
        [post_payload(i) for i in ids],
        expected_ids=ids,
    )
    assert batch.status == BatchStatus.COMPLETE.value
    assert batch.expected_count == 3
    assert batch.arrived_count == 3
    assert batch.inserted_count == 3
    assert batch.duplicate_count == 0
    assert batch.missing_count == 0
    assert batch.received_at is not None
    # 新稿入库，状态为 fresh
    states = {p.external_id: p.ingest_state for p in db.scalars(select(Post)).all()}
    assert set(states.values()) == {PostIngestState.FRESH.value}


def test_partial_batch_marks_old_posts_carried_not_fresh(db, channel_factory):
    channel = channel_factory("boss")
    # 第一批完整：b1 b2 入库
    _receive(db, "boss", "B-001", [post_payload("b1"), post_payload("b2")], expected_ids=["b1", "b2"])
    # 第二批缺量：应有 4 条，只到 2 条，且采集器报 partial
    batch = _receive(
        db,
        "boss",
        "B-002",
        [post_payload("b3"), post_payload("b4")],
        expected_ids=["b1", "b2", "b3", "b4"],
        source_status="partial",
    )
    assert batch.status == BatchStatus.PARTIAL.value
    assert batch.expected_count == 4
    assert batch.arrived_count == 2
    assert batch.missing_count == 2
    assert set(batch.missing_external_ids) == {"b1", "b2"}

    posts = {p.external_id: p.ingest_state for p in db.scalars(select(Post)).all()}
    # 本批实到新稿仍是新稿
    assert posts["b3"] == PostIngestState.FRESH.value
    assert posts["b4"] == PostIngestState.FRESH.value
    # 旧岗位只能沿用，绝不冒充新稿
    assert posts["b1"] == PostIngestState.CARRIED.value
    assert posts["b2"] == PostIngestState.CARRIED.value
    assert batch.carried_count == 2


def test_in_batch_duplicate_and_invalid_are_counted(db, channel_factory):
    channel_factory("boss")
    posts = [
        post_payload("b1"),
        post_payload("b1"),  # 批内重复
        {"external_id": "b9", "company_raw": "", "title_raw": "x", "city": "北京", "salary_raw": "10K"},  # 无效
    ]
    batch = _receive(db, "boss", "B-001", posts, expected=3)
    assert batch.arrived_count == 1  # 只有 b1 一条有效实到
    assert batch.inserted_count == 1
    assert batch.duplicate_count == 1
    assert batch.invalid_count == 1
    assert batch.status == BatchStatus.PARTIAL.value  # 缺 2 条
    outcomes = {item.external_id: item.outcome for item in db.scalars(select(IngestItem)).all()}
    assert outcomes["b1"] in {ItemOutcome.INSERTED.value, ItemOutcome.DUPLICATE.value}
    assert outcomes["b9"] == ItemOutcome.INVALID.value
    # 无效岗位不入库，岗位记录不出现 b9
    assert db.scalar(select(func.count()).select_from(Post).where(Post.external_id == "b9")) == 0


# ---------------------------------------------------------------- 2. 重试幂等
def test_retry_same_batch_does_not_duplicate_posts(db, channel_factory):
    channel = channel_factory("boss")
    ids = ["b1", "b2", "b3"]
    first = _receive(db, "boss", "B-001", [post_payload(i) for i in ids], expected_ids=ids)
    before = db.scalar(select(func.count()).select_from(Post))
    first_received = first.received_at

    # 用同一 batch_no 再传一次（模拟采集器重试），回执里还混了一条批内重复
    retried = _receive(
        db,
        "boss",
        "B-001",
        [post_payload("b1"), post_payload("b2"), post_payload("b3"), post_payload("b2")],
        expected_ids=ids,
    )
    after = db.scalar(select(func.count()).select_from(Post))
    assert after == before  # 岗位不重复新增
    assert retried.id == first.id
    assert retried.retries == 1
    assert retried.received_at == first_received  # 首次接收时间保留
    assert retried.inserted_count == 0
    assert retried.duplicate_count == 4  # 三条自然键已存在 + 一条批内重复
    assert retried.arrived_count == 3
    # 逐条回执重建，原始回执不丢
    assert db.scalar(select(func.count()).select_from(IngestItem).where(IngestItem.batch_id == first.id)) == 4
    assert retried.raw_receipt["batch_no"] == "B-001"


# ---------------------------------------------------------------- 3. 隔离与跨渠道
def test_failed_batch_isolates_channel_posts(db, channel_factory):
    channel = channel_factory("boss")
    _receive(db, "boss", "B-001", [post_payload("b1")], expected_ids=["b1"])
    failed = _receive(
        db, "boss", "B-002", [],
        expected_ids=["b1", "b2"], source_status="failed",
    )
    assert failed.status == BatchStatus.FAILED.value
    states = {p.external_id: p.ingest_state for p in db.scalars(select(Post)).all()}
    assert states["b1"] == PostIngestState.ISOLATED.value
    assert failed.isolated_count == 1


def test_other_channels_process_but_no_complete_edition(db, channel_factory):
    good = channel_factory("zhaopin")
    bad = channel_factory("lagou")
    # 好渠道收齐（独立公司，保证与坏渠道不聚成同簇）
    _receive(
        db, "zhaopin", "Z-001",
        [post_payload("z1", company_raw="智联优选科技有限公司"),
         post_payload("z2", company_raw="智联优选科技有限公司", title_raw="前端开发工程师")],
        expected_ids=["z1", "z2"],
    )
    # 坏渠道先有一版完整旧稿，再报一个缺量新批，旧稿被沿用
    _receive(
        db, "lagou", "L-000",
        [post_payload("l0", company_raw="拉勾智造网络有限公司")],
        expected_ids=["l0"],
    )
    _receive(
        db, "lagou", "L-001",
        [post_payload("l1", company_raw="拉勾云启数据有限公司")],
        expected_ids=["l1", "l2"], source_status="timeout",
    )

    ledger = ingest_service.channel_ledger(db)
    by_code = {row["channel_code"]: row for row in ledger["items"]}
    assert by_code["zhaopin"]["status"] == BatchStatus.COMPLETE.value
    assert by_code["zhaopin"]["release_state"] == ReleaseState.RELEASABLE.value
    assert by_code["lagou"]["status"] == BatchStatus.PARTIAL.value
    assert by_code["lagou"]["release_state"] == ReleaseState.CARRIED.value
    assert by_code["lagou"]["missing_count"] == 1
    assert by_code["lagou"]["carried_count"] == 1
    assert ledger["totals"]["publish_complete"] is False

    run = run_service.create_and_execute(db, trigger="manual", operator_email="admin@jobmerge.io")
    # 好渠道照常处理出簇，但整版不是完整发布版
    assert run.publish_complete is False
    assert run.clusters_out >= 2
    # 含沿用成员的簇被暂缓
    withheld = db.scalars(select(Cluster).where(Cluster.run_id == run.id, Cluster.release_state == ReleaseState.WITHHELD.value)).all()
    assert any(c.carried_member_count >= 1 for c in withheld)
    assert run.withheld_clusters >= 1
    assert run.releasable_clusters + run.withheld_clusters == run.clusters_out


def test_isolated_posts_excluded_from_merge(db, channel_factory):
    channel_factory("boss")
    _receive(db, "boss", "B-001", [post_payload("b1"), post_payload("b2")], expected_ids=["b1", "b2"])
    _receive(db, "boss", "B-002", [], expected_ids=["b3"], source_status="failed")
    run = run_service.create_and_execute(db, trigger="manual", operator_email="admin@jobmerge.io")
    # 隔离岗位仍在 posts 表（不丢失），但不进归并
    assert db.scalar(select(func.count()).select_from(Post)) == 2
    member_ids = set()
    for cluster in db.scalars(select(Cluster).where(Cluster.run_id == run.id)).all():
        member_ids.update(cluster.member_ids or [])
    isolated_posts = db.scalars(select(Post).where(Post.ingest_state == PostIngestState.ISOLATED.value)).all()
    assert all(p.id not in member_ids for p in isolated_posts)


# ---------------------------------------------------------------- 4. 全部收齐 → 完整发布版
def test_all_channels_complete_produces_full_edition(db, channel_factory):
    channel_factory("zhaopin")
    channel_factory("lagou")
    _receive(db, "zhaopin", "Z-001", [post_payload("z1")], expected_ids=["z1"])
    _receive(db, "lagou", "L-001", [post_payload("l1")], expected_ids=["l1"])
    run = run_service.create_and_execute(db, trigger="manual", operator_email="admin@jobmerge.io")
    assert run.publish_complete is True
    assert run.withheld_clusters == 0
    assert run.releasable_clusters == run.clusters_out


def test_recovery_after_retry_completes_partial_batch(db, channel_factory):
    channel_factory("boss")
    # 先缺量
    partial = _receive(
        db, "boss", "B-001", [post_payload("b1")],
        expected_ids=["b1", "b2"], source_status="partial",
    )
    assert partial.status == BatchStatus.PARTIAL.value
    # 用新批次补齐
    complete = _receive(db, "boss", "B-002", [post_payload("b2")], expected_ids=["b2"])
    assert complete.status == BatchStatus.COMPLETE.value
    states = {p.external_id: p.ingest_state for p in db.scalars(select(Post)).all()}
    assert states["b2"] == PostIngestState.FRESH.value
    # 上一批的旧稿转为历史沿用（数据仍在）
    assert states["b1"] == PostIngestState.CARRIED.value
    ledger = ingest_service.channel_ledger(db)
    assert ledger["totals"]["publish_complete"] is True


def test_recovery_from_failed_batch_via_retry_restores_fresh(db, channel_factory):
    """失败批次把岗位隔离；同批次号重试补到后，到达的岗位恢复新稿，不被当成沿用。"""
    channel_factory("boss")
    _receive(db, "boss", "B-001", [post_payload("b1")], expected_ids=["b1"])
    # 同批次号先报失败（模拟先超时失败）
    failed = _receive(db, "boss", "B-001", [], expected_ids=["b1"], source_status="failed")
    assert failed.status == BatchStatus.FAILED.value
    assert db.scalar(select(Post).where(Post.external_id == "b1")).ingest_state == PostIngestState.ISOLATED.value
    # 同批次号重试，b1 重新到达（自然键已存在 → duplicate，但确属本批到达）
    recovered = _receive(db, "boss", "B-001", [post_payload("b1")], expected_ids=["b1"])
    assert recovered.status == BatchStatus.COMPLETE.value
    post = db.scalar(select(Post).where(Post.external_id == "b1"))
    assert post.ingest_state == PostIngestState.FRESH.value
    assert recovered.inserted_count == 0  # 没有重复新增
    assert recovered.duplicate_count == 1
    assert recovered.arrived_count == 1
    ledger = ingest_service.channel_ledger(db)
    assert ledger["totals"]["publish_complete"] is True
    assert ledger["totals"]["isolated_posts"] == 0


# ---------------------------------------------------------------- 5. 发布清单与计数对账
def test_clusters_csv_complete_only_matches_releasable_count(db, channel_factory):
    channel_factory("zhaopin")
    channel_factory("lagou")
    _receive(
        db, "zhaopin", "Z-001",
        [post_payload("z1", company_raw="智联优选科技有限公司"),
         post_payload("z2", company_raw="智联优选科技有限公司", title_raw="前端开发工程师")],
        expected_ids=["z1", "z2"],
    )
    # 坏渠道：先有旧稿，再缺量 → 旧稿沿用，相关簇进暂缓清单
    _receive(db, "lagou", "L-000", [post_payload("l0", company_raw="拉勾智造网络有限公司")], expected_ids=["l0"])
    _receive(
        db, "lagou", "L-001",
        [post_payload("l1", company_raw="拉勾云启数据有限公司")],
        expected_ids=["l1", "l2"], source_status="partial",
    )
    run = run_service.create_and_execute(db, trigger="manual", operator_email="admin@jobmerge.io")

    full_csv = export_service.clusters_csv(db, run.id, complete_only=False).decode("utf-8")
    complete_csv = export_service.clusters_csv(db, run.id, complete_only=True).decode("utf-8")
    # 全量行数 = 归并簇数 + 表头
    full_data_lines = [line for line in full_csv.splitlines() if line][1:]
    complete_lines = [line for line in complete_csv.splitlines() if line][1:]
    assert len(full_data_lines) == run.clusters_out
    assert len(complete_lines) == run.releasable_clusters
    assert len(complete_lines) < len(full_data_lines)
    # 完整清单里不能出现暂缓发布口径
    assert "暂缓发布" not in complete_csv
    assert "暂缓发布" in full_csv


def test_batches_csv_and_ledger_match_counts(db, channel_factory):
    channel_factory("boss")
    _receive(db, "boss", "B-001", [post_payload("b1"), post_payload("b2")], expected_ids=["b1", "b2", "b3"], source_status="partial")
    csv_text = export_service.batches_csv(db).decode("utf-8")
    assert "本批缺量" in csv_text
    assert "B-001" in csv_text
    ledger = ingest_service.channel_ledger(db)
    row = next(item for item in ledger["items"] if item["channel_code"] == "boss")
    assert row["expected_count"] == 3
    assert row["arrived_count"] == 2
    assert row["missing_count"] == 1


# ---------------------------------------------------------------- 6. 原始回执不丢失
def test_raw_receipt_and_items_are_persisted(db, channel_factory):
    channel_factory("boss")
    raw_posts = [post_payload("b1"), post_payload("b2")]
    batch = _receive(db, "boss", "B-001", raw_posts, expected_ids=["b1", "b2"])
    # 整包原始回执
    assert batch.raw_receipt["posts"] == raw_posts
    # 逐条回执
    items = db.scalars(select(IngestItem).where(IngestItem.batch_id == batch.id).order_by(IngestItem.seq)).all()
    assert [item.external_id for item in items] == ["b1", "b2"]
    assert all(item.raw_item["salary_raw"] == "10-15K" for item in items)
    # 岗位记录完整
    posts = db.scalars(select(Post).order_by(Post.external_id)).all()
    assert [p.external_id for p in posts] == ["b1", "b2"]
    assert all(p.salary_status == "parsed" for p in posts)


def test_get_batch_detail_returns_reconciled_items(db, channel_factory):
    channel_factory("boss")
    batch = _receive(db, "boss", "B-001", [post_payload("b1")], expected_ids=["b1", "b2"], source_status="partial")
    _, channel, items = ingest_service.get_batch(db, batch.id)
    assert channel.code == "boss"
    assert len(items) == 1
    serialized = ingest_service.serialize_batch(batch, channel, items)
    assert serialized["reconciled"] is True
    assert serialized["missing_external_ids"] == ["b2"]
    assert serialized["items"][0]["outcome"] == ItemOutcome.INSERTED.value
