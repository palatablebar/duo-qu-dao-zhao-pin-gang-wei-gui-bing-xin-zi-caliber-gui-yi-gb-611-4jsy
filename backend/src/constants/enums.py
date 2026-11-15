"""共享枚举（后端唯一定义处）。前端副本见 frontend/site/js/config.js。

README「共享枚举出现位置」一节列出全部出现点，改动需两侧同步。
"""
from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    ANALYST = "analyst"
    VIEWER = "viewer"


class ChannelKind(StrEnum):
    """渠道接入方式：招聘站点 / 接口对接 / 文件导入 / 手工登记。"""

    SITE = "site"
    API = "api"
    FILE = "file"
    MANUAL = "manual"


CHANNEL_KIND_LABELS: dict[str, str] = {
    ChannelKind.SITE.value: "招聘站点",
    ChannelKind.API.value: "接口对接",
    ChannelKind.FILE.value: "文件导入",
    ChannelKind.MANUAL.value: "手工登记",
}


class ParseStatus(StrEnum):
    """薪资口径归一结果：可比较 / 面议 / 不可解析。"""

    PARSED = "parsed"
    NEGOTIABLE = "negotiable"
    UNPARSED = "unparsed"


PARSE_STATUS_LABELS: dict[str, str] = {
    ParseStatus.PARSED.value: "已归一",
    ParseStatus.NEGOTIABLE.value: "面议",
    ParseStatus.UNPARSED.value: "不可比",
}

PARSE_STATUS_ORDER: list[str] = [
    ParseStatus.PARSED.value,
    ParseStatus.NEGOTIABLE.value,
    ParseStatus.UNPARSED.value,
]


class ClusterStatus(StrEnum):
    """归并簇状态：在架发布 / 已过期下线 / 已拆散待重算。"""

    ACTIVE = "active"
    EXPIRED = "expired"
    MERGED = "merged"


CLUSTER_STATUS_LABELS: dict[str, str] = {
    ClusterStatus.ACTIVE.value: "在架",
    ClusterStatus.EXPIRED.value: "已过期",
    ClusterStatus.MERGED.value: "已拆散待重算",
}

CLUSTER_STATUS_ORDER: list[str] = [
    ClusterStatus.ACTIVE.value,
    ClusterStatus.EXPIRED.value,
    ClusterStatus.MERGED.value,
]


class ReviewDecision(StrEnum):
    MERGE = "merge"
    SPLIT = "split"
    KEEP = "keep"


REVIEW_DECISION_LABELS: dict[str, str] = {
    ReviewDecision.MERGE.value: "确认合并",
    ReviewDecision.SPLIT.value: "拆分为独立岗位",
    ReviewDecision.KEEP.value: "保持原样",
}

REVIEW_DECISION_ORDER: list[str] = [
    ReviewDecision.MERGE.value,
    ReviewDecision.SPLIT.value,
    ReviewDecision.KEEP.value,
]


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"


RUN_STATUS_LABELS: dict[str, str] = {
    RunStatus.QUEUED.value: "排队中",
    RunStatus.RUNNING.value: "执行中",
    RunStatus.SUCCESS.value: "成功",
    RunStatus.PARTIAL.value: "部分成功",
    RunStatus.FAILED.value: "失败",
}


class TriggerKind(StrEnum):
    MANUAL = "manual"
    SCHEDULE = "schedule"
    SEED = "seed"


TRIGGER_LABELS: dict[str, str] = {
    TriggerKind.MANUAL.value: "手动",
    TriggerKind.SCHEDULE.value: "定时",
    TriggerKind.SEED.value: "初始化",
}


class BatchStatus(StrEnum):
    """来稿批次状态：回执核对后的结论。

    complete  = 本批应到岗位全部到齐；
    partial   = 采集器报部分成功/超时，本批缺量；
    failed    = 本批完全失败，没有任何有效新稿。
    """

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


BATCH_STATUS_LABELS: dict[str, str] = {
    BatchStatus.COMPLETE.value: "本批收齐",
    BatchStatus.PARTIAL.value: "本批缺量",
    BatchStatus.FAILED.value: "本批失败",
}

BATCH_STATUS_ORDER: list[str] = [
    BatchStatus.COMPLETE.value,
    BatchStatus.PARTIAL.value,
    BatchStatus.FAILED.value,
]


class ItemOutcome(StrEnum):
    """批次内单条回执的落库结果。"""

    INSERTED = "inserted"   # 新岗位，已入库
    DUPLICATE = "duplicate"  # 重复回执（批内重复或自然键已存在），未新增
    INVALID = "invalid"      # 缺必填字段，隔离不入库


ITEM_OUTCOME_LABELS: dict[str, str] = {
    ItemOutcome.INSERTED.value: "新稿入库",
    ItemOutcome.DUPLICATE.value: "重复未新增",
    ItemOutcome.INVALID.value: "无效隔离",
}


class PostIngestState(StrEnum):
    """原始岗位的来稿状态（按渠道最新批次口径）。

    fresh    = 最新批次的有效新稿；
    carried  = 旧批次岗位，因最新批次缺量而暂沿用（不得冒充新稿）；
    isolated = 失败/无效批次里的岗位，隔离不参与归并与发布。
    """

    FRESH = "fresh"
    CARRIED = "carried"
    ISOLATED = "isolated"


POST_INGEST_LABELS: dict[str, str] = {
    PostIngestState.FRESH.value: "本批新稿",
    PostIngestState.CARRIED.value: "旧稿沿用",
    PostIngestState.ISOLATED.value: "隔离",
}


class ReleaseState(StrEnum):
    """渠道/归并簇的发布口径。"""

    RELEASABLE = "releasable"   # 渠道收齐，可进完整发布版
    CARRIED = "carried"         # 含旧稿沿用，仅过渡版
    ISOLATED = "isolated"       # 渠道失败/岗位隔离，不发布
    WITHHELD = "withheld"       # 簇含沿用/隔离成员，不进完整发布清单


RELEASE_STATE_LABELS: dict[str, str] = {
    ReleaseState.RELEASABLE.value: "可发布",
    ReleaseState.CARRIED.value: "旧稿沿用·过渡",
    ReleaseState.ISOLATED.value: "隔离不发布",
    ReleaseState.WITHHELD.value: "暂缓发布",
}


class Degree(StrEnum):
    ANY = "any"
    COLLEGE = "college"
    BACHELOR = "bachelor"
    MASTER = "master"
    DOCTOR = "doctor"


DEGREE_LABELS: dict[str, str] = {
    Degree.ANY.value: "不限",
    Degree.COLLEGE.value: "大专",
    Degree.BACHELOR.value: "本科",
    Degree.MASTER.value: "硕士",
    Degree.DOCTOR.value: "博士",
}

DEGREE_ORDER: list[str] = [item.value for item in Degree]


class Experience(StrEnum):
    ANY = "any"
    JUNIOR = "1-3"
    MIDDLE = "3-5"
    SENIOR = "5-10"
    EXPERT = "10+"


EXPERIENCE_LABELS: dict[str, str] = {
    Experience.ANY.value: "不限",
    Experience.JUNIOR.value: "1-3 年",
    Experience.MIDDLE.value: "3-5 年",
    Experience.SENIOR.value: "5-10 年",
    Experience.EXPERT.value: "10 年以上",
}

EXPERIENCE_ORDER: list[str] = [item.value for item in Experience]


# 薪资区间的元数据：用于前端下拉与后端校验
DEGREE_UNKNOWN_LABEL = "未标注"
EXPERIENCE_UNKNOWN_LABEL = "未标注"
