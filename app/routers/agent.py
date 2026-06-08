"""REQ-02 — AI 에이전트 서빙 Tool API.

보안 철칙: 에이전트는 DB 직접 SQL·파일시스템 접근 금지.
Pydantic 으로 엄격히 정의된 계약 인자만 허용한다.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..auth import verify_serving
from ..dataset import DATASETS, DEFAULT_DATASET, json_schema, json_schema_from
from ..iceberg_io import current_schema_fields
from ..query import run_query
from ..schemas import QueryRequest, QuerySummary

router = APIRouter(
    prefix="/agent/tools", tags=["agent"], dependencies=[Depends(verify_serving)]
)


@router.get("/datasets")
def datasets() -> dict:
    """사용 가능한 데이터셋과 파티션 키 목록."""
    return {name: {"partition_keys": ds.partition_keys, "measures": ds.measures}
            for name, ds in DATASETS.items()}


@router.get("/schema")
def schema(dataset: str = DEFAULT_DATASET) -> dict:
    """지정 dataset 의 현재 Iceberg 스키마를 JSON Schema 로 반환.

    스키마 진화로 추가된 컬럼이 자동 반영된다(테이블 미존재 시 기준 스키마).
    """
    if dataset not in DATASETS:
        raise HTTPException(status_code=404, detail=f"알 수 없는 dataset: {dataset}")
    fields = current_schema_fields(dataset)
    if fields:
        return json_schema_from(dataset, fields)
    return json_schema(dataset)


@router.post("/query", response_model=QuerySummary)
def query(req: QueryRequest) -> QuerySummary:
    """계약된 인자값으로 파티션 프루닝 조회 후 요약 JSON 반환."""
    return run_query(req)
