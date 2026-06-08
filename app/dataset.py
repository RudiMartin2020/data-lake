"""데이터셋 계약(스키마) 정의.

설계서의 파티션 키(production_date + line_id)를 따른다.
실제 운영에서는 PostgreSQL 카탈로그/Iceberg 스키마에서 동적으로 읽어오지만,
여기서는 테스트 가능한 단일 기준 데이터셋 'production'을 코드로 고정 정의한다.
"""
from __future__ import annotations

from typing import Dict, List

# 컬럼명 -> (타입, 설명)
PRODUCTION_COLUMNS: Dict[str, Dict[str, str]] = {
    "production_date": {"type": "date", "description": "생산일 (YYYY-MM-DD) · 파티션 키"},
    "line_id": {"type": "string", "description": "라인 ID (예: FAB-1) · 파티션 키"},
    "product_id": {"type": "string", "description": "제품 ID"},
    "qty": {"type": "integer", "description": "생산 수량"},
    "defect_qty": {"type": "integer", "description": "불량 수량"},
}

# 파티션 키 (디렉터리 계층 순서)
PARTITION_KEYS: List[str] = ["production_date", "line_id"]

# 적재 시 반드시 존재해야 하는 컬럼
REQUIRED_COLUMNS: List[str] = list(PRODUCTION_COLUMNS.keys())

DATASET_NAME = "production"


def json_schema() -> dict:
    """기준(정적) JSON Schema — Iceberg 테이블이 아직 없을 때 폴백."""
    properties = {
        col: {"type": _json_type(meta["type"]), "description": meta["description"]}
        for col, meta in PRODUCTION_COLUMNS.items()
    }
    return {
        "dataset": DATASET_NAME,
        "partition_keys": PARTITION_KEYS,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": REQUIRED_COLUMNS,
        },
    }


def json_schema_from(fields) -> dict:
    """Iceberg 현재 스키마(list of (name, iceberg_type_str))로 동적 생성.

    스키마 진화로 추가된 컬럼이 자동 반영된다.
    """
    properties = {}
    for name, itype in fields:
        desc = PRODUCTION_COLUMNS.get(name, {}).get("description", "")
        properties[name] = {"type": _iceberg_json_type(itype), "description": desc}
    return {
        "dataset": DATASET_NAME,
        "partition_keys": PARTITION_KEYS,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": REQUIRED_COLUMNS,
        },
    }


def _json_type(t: str) -> str:
    return {
        "date": "string",
        "string": "string",
        "integer": "integer",
        "float": "number",
    }.get(t, "string")


def _iceberg_json_type(t: str) -> str:
    t = t.lower()
    if t in ("int", "long"):
        return "integer"
    if t in ("float", "double") or t.startswith("decimal"):
        return "number"
    if t == "boolean":
        return "boolean"
    return "string"  # string, date, timestamp 등
