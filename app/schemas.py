"""Pydantic 계약 모델 — 에이전트 격리(화이트리스트 입력)의 핵심."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

# ----------------------------- Ingestion -----------------------------


class IngestAccepted(BaseModel):
    """POST /ingest 202 응답."""
    task_id: str
    status: str = "accepted"
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
    """
    production_date: date = Field(..., description="생산일 (YYYY-MM-DD)")
    line_id: str = Field(..., description="라인 ID (예: FAB-1)")
    limit: int = Field(1000, ge=1, description="스캔 행 상한(과다 조회 방지)")

    @field_validator("line_id")
    @classmethod
    def _validate_line_id(cls, v: str) -> str:
        # 화이트리스트 패턴: 영문/숫자/하이픈/언더스코어만 허용 → 경로 주입·인젝션 방지
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", v):
            raise ValueError("line_id 는 영문/숫자/-/_ 1~64자만 허용됩니다.")
        return v

    @field_validator("limit")
    @classmethod
    def _cap_limit(cls, v: int) -> int:
        # 설정된 상한(QUERY_ROW_LIMIT)으로 클램프
        from .config import settings

        return min(v, settings.query_row_limit)


class QuerySummary(BaseModel):
    """POST /agent/tools/query 응답 — 토큰 절약용 요약 JSON."""
    dataset: str
    production_date: date
    line_id: str
    found: bool
    row_count: int = 0
    total_qty: int = 0
    total_defect_qty: int = 0
    defect_rate: float = 0.0
    products: List[Dict[str, Any]] = Field(default_factory=list)
    note: Optional[str] = None
