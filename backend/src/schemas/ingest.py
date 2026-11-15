"""来稿批次入参校验：采集器回执 + 应到清单。"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from src.constants.limits import MAX_SALARY_RAW_LENGTH


class IngestPostIn(BaseModel):
    external_id: str = Field(min_length=1, max_length=60)
    company_raw: str = Field(min_length=1, max_length=160)
    title_raw: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=40)
    salary_raw: str = Field(min_length=1, max_length=MAX_SALARY_RAW_LENGTH)
    degree: str = Field(default="不限", max_length=40)
    experience: str = Field(default="不限", max_length=40)


class IngestIn(BaseModel):
    channel_code: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    batch_no: str = Field(min_length=1, max_length=60)
    # 应有数：给 expected_external_ids 时以清单长度为准；两者都给必须一致
    expected_count: int | None = Field(default=None, ge=0)
    expected_external_ids: list[str] | None = Field(default=None, max_length=5000)
    source_status: Literal["success", "partial", "timeout", "failed"] = "success"
    posts: list[IngestPostIn] = Field(default_factory=list, max_length=5000)
    note: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _check_expected(self) -> "IngestIn":
        if self.expected_count is None and not self.expected_external_ids:
            raise ValueError("expected_count 与 expected_external_ids 至少要给一个，用于核对应有数")
        if self.expected_external_ids is not None:
            ids = [item for item in (x.strip() for x in self.expected_external_ids) if item]
            if not ids:
                raise ValueError("expected_external_ids 不能为空清单")
            if self.expected_count is not None and self.expected_count != len(ids):
                raise ValueError("expected_count 与 expected_external_ids 长度不一致")
            self.expected_external_ids = ids
        if self.expected_count is not None and self.expected_count < len(self.posts):
            raise ValueError("实到岗位数不能大于应有数")
        return self
