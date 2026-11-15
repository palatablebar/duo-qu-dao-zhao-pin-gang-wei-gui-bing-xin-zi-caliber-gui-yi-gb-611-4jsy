"""归并运行：读取全部启用渠道的原始岗位 → 算法 A 汇总口径 → 算法 B 聚类 → 落库。

发布口径（来稿批次约束）：
- ingest_state=isolated 的岗位（失败/无效批次）不参与归并；
- 只要还有渠道本批缺量（partial）或失败（failed），整次运行不是完整发布版（publish_complete=False），
  其他渠道照常处理，但输出只能作为过渡版；
- 含旧稿沿用（carried）成员的簇标记 withheld，不进完整发布清单；
- 渠道批次全部收齐时，完整发布版只放 release_state=releasable 的簇。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.constants.enums import BatchStatus, PostIngestState, ReleaseState
from src.models.channel import Channel
from src.models.cluster import Cluster
from src.models.post import Post
from src.models.run import Run
from src.services import dedupe, ingest_service
from src.utils.timeutil import local_now, local_stamp


def _post_rows(db: Session) -> list[tuple[Post, Channel]]:
    stmt = (
        select(Post, Channel)
        .join(Channel, Post.channel_id == Channel.id)
        .where(Channel.enabled.is_(True))
        .order_by(Post.id)
    )
    return [(post, channel) for post, channel in db.execute(stmt).all()]


def _eligible_posts(rows: list[tuple[Post, Channel]]) -> list[tuple[Post, Channel]]:
    """隔离岗位不参与归并；其余（新稿 + 旧稿沿用）保留，旧稿在簇级标注暂缓。"""
    return [(post, channel) for post, channel in rows if post.ingest_state != PostIngestState.ISOLATED.value]


def _channel_publish_view(db: Session, channel_ids: set[int]) -> dict:
    """每个渠道最新批次 → 是否收齐；返回 publish_complete 与各渠道发布状态。"""
    if not channel_ids:
        return {"publish_complete": True, "by_channel": {}}
    latest = ingest_service.latest_batch_map(db)
    by_channel: dict[int, str] = {}
    publish_complete = True
    for channel_id in channel_ids:
        batch = latest.get(channel_id)
        if batch is None:
            # 有岗位但没有任何批次记录（历史数据）：不阻断，按可发布处理
            by_channel[channel_id] = ReleaseState.RELEASABLE.value
            continue
        if batch.status == BatchStatus.COMPLETE.value:
            by_channel[channel_id] = ReleaseState.RELEASABLE.value
        elif batch.status == BatchStatus.FAILED.value:
            by_channel[channel_id] = ReleaseState.ISOLATED.value
            publish_complete = False
        else:
            by_channel[channel_id] = ReleaseState.CARRIED.value
            publish_complete = False
    return {"publish_complete": publish_complete, "by_channel": by_channel}


def _payload(post: Post, channel: Channel) -> dict:
    return {
        "id": post.id,
        "channel_id": post.channel_id,
        "channel_code": channel.code,
        "channel_name": channel.name,
        "company_raw": post.company_raw,
        "company_norm": post.company_norm,
        "title_raw": post.title_raw,
        "title_stem": post.title_stem,
        "city": post.city,
        "salary_raw": post.salary_raw,
        "monthly_low": post.monthly_low,
        "monthly_high": post.monthly_high,
        "monthly_median": post.monthly_median,
        "months": post.months,
        "degree": post.degree,
        "experience": post.experience,
        "published_at": post.published_at,
        "expire_at": post.expire_at,
        "raw_text": post.raw_text,
        "ingest_state": post.ingest_state or PostIngestState.FRESH.value,
    }


CLUSTER_FIELDS = (
    "blocking_key",
    "company_norm",
    "company_display",
    "title_stem",
    "title_display",
    "city",
    "salary_low",
    "salary_high",
    "monthly_median",
    "salary_basis",
    "member_ids",
    "channels",
    "channel_count",
    "member_count",
    "dup_removed",
    "degree",
    "experience",
    "published_at",
    "expire_at",
    "status",
    "confidence",
    "needs_review",
    "review_reason",
    "golden_post_id",
)


def _release_for_cluster(item: dict, post_state: dict[int, str]) -> tuple[str, int, int, str]:
    """按成员来稿状态给簇发布口径：含隔离/沿用成员即暂缓发布。"""
    members = post_state  # alias for clarity below
    states = [members.get(pid, PostIngestState.FRESH.value) for pid in item["member_ids"]]
    isolated = sum(1 for state in states if state == PostIngestState.ISOLATED.value)
    carried = sum(1 for state in states if state == PostIngestState.CARRIED.value)
    notes = []
    if carried:
        notes.append(f"含 {carried} 条旧稿沿用")
    if isolated:
        notes.append(f"含 {isolated} 条隔离岗位")
    if isolated or carried:
        return ReleaseState.WITHHELD.value, carried, isolated, "；".join(notes)
    return ReleaseState.RELEASABLE.value, 0, 0, ""


def execute_merge(db: Session, run: Run) -> dict:
    all_rows = _post_rows(db)
    isolated_rows = [(post, _) for post, _ in all_rows if post.ingest_state == PostIngestState.ISOLATED.value]
    rows = _eligible_posts(all_rows)
    payloads = [_payload(post, channel) for post, channel in rows]
    post_state = {post.id: (post.ingest_state or PostIngestState.FRESH.value) for post, _ in rows}
    channel_ids = {channel.id for _, channel in rows}
    publish_view = _channel_publish_view(db, channel_ids)
    result = dedupe.build_clusters(payloads, threshold=run.threshold or dedupe.DEFAULT_THRESHOLD)
    clusters = result["clusters"]

    existing = {
        cluster.fingerprint: cluster
        for cluster in db.scalars(select(Cluster).where(Cluster.run_id == run.id)).all()
    }
    withheld_count = 0
    for item in clusters:
        cluster = existing.pop(item["fingerprint"], None)
        if cluster is None:
            cluster = Cluster(run_id=run.id, fingerprint=item["fingerprint"])
            db.add(cluster)
        for field in CLUSTER_FIELDS:
            setattr(cluster, field, item[field])
        release_state, carried_members, isolated_members, release_note = _release_for_cluster(
            item, post_state
        )
        cluster.release_state = release_state
        cluster.carried_member_count = carried_members
        cluster.isolated_member_count = isolated_members
        cluster.release_note = release_note
        if release_state == ReleaseState.WITHHELD.value:
            withheld_count += 1
    for leftover in existing.values():
        db.delete(leftover)
    db.flush()

    parsed = sum(1 for post, _ in rows if post.salary_status == "parsed")
    negotiable = sum(1 for post, _ in rows if post.salary_status == "negotiable")
    unparsed_posts = [post for post, _ in rows if post.salary_status == "unparsed"]
    total = len(rows)
    isolated_total = len(isolated_rows)
    carried_total = sum(1 for post, _ in rows if post.ingest_state == PostIngestState.CARRIED.value)
    fresh_total = sum(1 for post, _ in rows if post.ingest_state == PostIngestState.FRESH.value)
    channel_codes = sorted({channel.code for _, channel in rows})
    releasable_count = len(clusters) - withheld_count
    publish_complete = publish_view["publish_complete"]

    metrics = {
        "posts_in": total,
        "channels": channel_codes,
        "channel_count": len(channel_codes),
        "parsed": parsed,
        "negotiable": negotiable,
        "unparsed": len(unparsed_posts),
        "parse_fail_rate": round(len(unparsed_posts) / total, 4) if total else 0.0,
        "noncomparable_rate": round((negotiable + len(unparsed_posts)) / total, 4) if total else 0.0,
        "blocks": result["metrics"]["blocks"],
        "pairs": result["metrics"]["pairs"],
        "edges": result["metrics"]["edges"],
        "clusters": result["metrics"]["clusters"],
        "multi_clusters": result["metrics"]["multi_clusters"],
        "dupes_removed": result["metrics"]["dupes_removed"],
        "largest_cluster": result["metrics"]["largest_cluster"],
        "needs_review": result["metrics"]["needs_review"],
        "expired_clusters": result["metrics"]["expired_clusters"],
        "threshold": run.threshold,
        "avg_confidence": round(
            sum(item["confidence"] for item in clusters) / len(clusters), 4
        )
        if clusters
        else 0.0,
        "unparsed_samples": [
            {"raw": post.salary_raw, "reason": (post.salary_basis or "").split("：")[-1][:60]}
            for post in unparsed_posts[:5]
        ],
        "publish_complete": publish_complete,
        "releasable_clusters": releasable_count,
        "withheld_clusters": withheld_count,
        "fresh_posts": fresh_total,
        "carried_posts": carried_total,
        "isolated_posts": isolated_total,
        "channel_release": [
            {"channel_id": cid, "release_state": state}
            for cid, state in sorted(publish_view["by_channel"].items())
        ],
    }
    edition_word = "完整发布版" if publish_complete else "过渡版（有渠道本批未收齐，不发布成完整版本）"
    log_lines = [
        f"归并运行 {run.run_no} 开始于 {local_stamp(local_now())}，阈值 {run.threshold:.2f}",
        f"输入原始岗位 {total} 条，来自 {len(channel_codes)} 个渠道：{'、'.join(channel_codes)}"
        f"（新稿 {fresh_total} / 旧稿沿用 {carried_total} / 隔离不参与 {isolated_total}）",
        f"薪资口径：已归一 {parsed} / 面议 {negotiable} / 不可比 {len(unparsed_posts)}"
        f"（不可比占比 {metrics['noncomparable_rate'] * 100:.2f}%）",
        f"分块 {metrics['blocks']} 个，两两比较 {metrics['pairs']} 对，连边 {metrics['edges']} 条",
        f"输出 {metrics['clusters']} 簇（多成员 {metrics['multi_clusters']} 簇，最大 {metrics['largest_cluster']} 条），"
        f"消重 {metrics['dupes_removed']} 条",
        f"待人工复核 {metrics['needs_review']} 簇，已过期 {metrics['expired_clusters']} 簇",
        f"发布口径：可发布 {releasable_count} 簇 / 暂缓 {withheld_count} 簇；本次结论：{edition_word}",
    ]
    # 数据本身为空仍是 partial；批次缺量不改变运行状态，但通过 publish_complete 表达
    status = "success" if total and clusters else "partial"
    run.publish_complete = publish_complete
    run.releasable_clusters = releasable_count
    run.withheld_clusters = withheld_count
    return {"metrics": metrics, "log": "\n".join(log_lines), "status": status}
