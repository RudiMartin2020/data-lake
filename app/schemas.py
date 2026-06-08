"""Pydantic 계약 모델 — 에이전트 격리(화이트리스트 입력)의 핵심."""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

from .dataset import DATASETS, DEFAULT_DATASET

_VALUE_RE = re.compile(r"[A-Za-z0-9_:-]{1,64}")  # 날짜(2026-05-29)·ID(FAB-1) 허용, 경로/주입 차단

# ----------------------------- Ingestion -----------------------------


class IngestAccepted(BaseModel):
    """POST /ingest 202 응답."""
    task_id: str
    status: str = "accepted"
    dataset: str = DEFAULT_DATASET
    source_id: str
    filename: str
    content_hash: str
    duplicate: bool = Field(False, description="동일 content_hash 가 이미 적재된 경우 True")


class TaskStatus(BaseModel):
    """GET /ingest/status/{task_id} 응답."""
    task_id: str
    status: str  # accepted | processing | done | failed | duplicate
    dataset: Optional[str] = None
    rows: Optional[int] = None
    partitions: Optional[int] = None
    error: Optional[str] = None


# ----------------------------- Agent Tool API -----------------------------


class QueryRequest(BaseModel):
    """POST /agent/tools/query 요청.

    자연어가 아닌 *계약된 인자값* 만 허용한다(임의 SQL 차단).
    filters 의 키는 해당 dataset 의 파티션 키와 일치해야 한다.
    """
    dataset: str = Field(DEFAULT_DATASET, description="데이터셋 이름")
    filters: Dict[str, str] = Field(..., description="파티션 키 → 값 (예: {production_date, line_id})")
    limit: int = Field(1000, ge=1, description="스캔 행 상한(과다 조회 방지)")

    @field_validator("dataset")
    @classmethod
    def _validate_dataset(cls, v: str) -> str:
        if v not in DATASETS:
            raise ValueError(f"알 수 없는 dataset: {v} (가능: {list(DATASETS)})")
        return v

    @field_validator("filters")
    @classmethod
    def _validate_filters(cls, v: Dict[str, str], info) -> Dict[str, str]:
        ds_name = info.data.get("dataset", DEFAULT_DATASET)
        ds = DATASETS.get(ds_name)
        if ds is None:
            return v  # dataset 검증에서 이미 실패 처리
        if set(v.keys()) != set(ds.partition_keys):
            raise ValueError(f"filters 키는 파티션 키 {ds.partition_keys} 와 일치해야 합니다.")
        for key, val in v.items():
            if not _VALUE_RE.fullmatch(str(val)):
                raise ValueError(f"filter 값 '{val}' 이 허용 패턴(영문/숫자/-/_/:)을 벗어납니다.")
        return v

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v: int) -> int:
        from .config import settings

        return min(v, settings.query_row_limit)


class QuerySummary(BaseModel):
    """POST /agent/tools/query 응답 — 토큰 절약용 요약 JSON.

    데이터셋 무관 일반 형태: totals(measure 합계) + groups(그룹별 합계).
    """
    dataset: str
    filters: Dict[str, str]
    found: bool
    row_count: int = 0
    totals: Dict[str, Any] = Field(default_factory=dict)         # measure -> 합계(int/float)
    groups: List[Dict[str, Any]] = Field(default_factory=list)   # [{group_by: 값, measure: 합계,...}]
    note: Optional[str] = None
