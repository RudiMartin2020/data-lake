"""데이터셋 레지스트리 (멀티 데이터셋).

각 데이터셋은 파티션 키·필수 컬럼·집계(measure)·그룹키를 선언한다.
새 데이터셋은 DATASETS 에 항목 하나만 추가하면 적재/조회가 동작한다.
실제 스키마(컬럼)는 Iceberg 테이블에서 동적으로 읽으며, 여기 columns 는
설명/타입 힌트와 폴백 스키마용이다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass(frozen=True)
class Dataset:
    name: str
    columns: Dict[str, Dict[str, str]]      # 컬럼명 -> {type, description}
    partition_keys: List[str]               # 파티션 키(계층 순서)
    measures: List[str]                     # 집계(SUM) 대상 숫자 컬럼
    group_by: str                           # 그룹 집계 기준 컬럼

    @property
    def required(self) -> List[str]:
        return list(self.columns.keys())

    def is_integer_measure(self, m: str) -> bool:
        return self.columns.get(m, {}).get("type") == "integer"


DATASETS: Dict[str, Dataset] = {
    "production": Dataset(
        name="production",
        columns={
            "production_date": {"type": "date", "description": "생산일 (YYYY-MM-DD) · 파티션 키"},
            "line_id": {"type": "string", "description": "라인 ID (예: FAB-1) · 파티션 키"},
            "product_id": {"type": "string", "description": "제품 ID"},
            "qty": {"type": "integer", "description": "생산 수량"},
            "defect_qty": {"type": "integer", "description": "불량 수량"},
        },
        partition_keys=["production_date", "line_id"],
        measures=["qty", "defect_qty"],
        group_by="product_id",
    ),
    "sensor_readings": Dataset(
        name="sensor_readings",
        columns={
            "reading_date": {"type": "date", "description": "측정일 (YYYY-MM-DD) · 파티션 키"},
            "sensor_id": {"type": "string", "description": "센서 ID (예: S-1) · 파티션 키"},
            "metric": {"type": "string", "description": "측정 항목 (예: temperature)"},
            "value": {"type": "float", "description": "측정값"},
        },
        partition_keys=["reading_date", "sensor_id"],
        measures=["value"],
        group_by="metric",
    ),
}

DEFAULT_DATASET = "production"


def get_dataset(name: str) -> Dataset:
    if name not in DATASETS:
        raise KeyError(name)
    return DATASETS[name]


def json_schema(dataset: str) -> dict:
    """기준(정적) JSON Schema — Iceberg 테이블이 아직 없을 때 폴백."""
    ds = DATASETS[dataset]
    return _schema_doc(ds, {
        col: {"type": _json_type(meta["type"]), "description": meta["description"]}
        for col, meta in ds.columns.items()
    })


def json_schema_from(dataset: str, fields) -> dict:
    """Iceberg 현재 스키마(list of (name, iceberg_type_str))로 동적 생성.

    스키마 진화로 추가된 컬럼이 자동 반영된다.
    """
    ds = DATASETS[dataset]
    props = {}
    for name, itype in fields:
        desc = ds.columns.get(name, {}).get("description", "")
        props[name] = {"type": _iceberg_json_type(itype), "description": desc}
    return _schema_doc(ds, props)


def _schema_doc(ds: Dataset, properties: dict) -> dict:
    return {
        "dataset": ds.name,
        "partition_keys": ds.partition_keys,
        "measures": ds.measures,
        "schema": {
            "type": "object",
            "properties": properties,
            "required": ds.required,
        },
    }


def _json_type(t: str) -> str:
    return {"date": "string", "string": "string", "integer": "integer", "float": "number"}.get(t, "string")


def _iceberg_json_type(t: str) -> str:
    t = t.lower()
    if t in ("int", "long"):
        return "integer"
    if t in ("float", "double") or t.startswith("decimal"):
        return "number"
    if t == "boolean":
        return "boolean"
    return "string"
