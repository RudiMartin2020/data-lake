"""REQ-02 — AI 에이전트 서빙 Tool API.

보안 철칙: 에이전트는 DB 직접 SQL·파일시스템 접근 금지.
Pydantic 으로 엄격히 정의된 계약 인자만 허용한다.
"""
from __future__ import annotations

from fastapi import APIRouter

from ..dataset import json_schema, json_schema_from
from ..iceberg_io import current_schema_fields
from ..query import run_query
from ..schemas import QueryRequest, QuerySummary

router = APIRouter(prefix="/agent/tools", tags=["agent"])


@router.get("/schema")
def schema() -> dict:
    """현재 Iceberg 테이블의 컬럼/메타데이터를 JSON Schema 로 반환.

    스키마 진화로 추가된 컬럼이 자동 반영된다(테이블 미존재 시 기준 스키마).
    """
    fields = current_schema_fields()
    if fields:
        return json_schema_from(fields)
    return json_schema()


@router.post("/query", response_model=QuerySummary)
def query(req: QueryRequest) -> QuerySummary:
    """계약된 인자값으로 파티션 프루닝 조회 후 요약 JSON 반환."""
    return run_query(req)
