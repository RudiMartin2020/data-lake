"""적재 처리: parse → validate → Iceberg 적재 → 카탈로그 기록.

Write Path 의 워커 본체. CSV 바이트를 PyArrow 로 읽어 검증한 뒤
Apache Iceberg 테이블에 append 한다(스키마 진화 포함). 실패 건은 DLQ 로 격리.
"""
from __future__ import annotations

import io
from typing import List

import pyarrow as pa
import pyarrow.csv as pacsv

from .catalog import catalog
from .config import settings
from .dataset import REQUIRED_COLUMNS
from .iceberg_io import append_arrow
from .storage import storage


class ValidationError(Exception):
    pass


def _read_csv(data: bytes) -> pa.Table:
    return pacsv.read_csv(io.BytesIO(data))


def _validate(names: List[str]) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in names]
    if missing:
        raise ValidationError(f"필수 컬럼 누락: {missing} (헤더={names})")


def process_ingestion(task_id: str, raw_key: str, raw_bytes: bytes) -> None:
    """단일 적재 작업 처리. 예외는 잡아 카탈로그에 failed/DLQ 로 기록."""
    catalog.update(task_id, status="processing")
    try:
        at = _read_csv(raw_bytes)
        _validate(at.schema.names)

        partitions = append_arrow(at)

        catalog.update(
            task_id,
            status="done",
            rows=at.num_rows,
            partitions=partitions,
        )
    except Exception as exc:  # noqa: BLE001 — DLQ 격리 목적
        # 파싱/적재 실패 건은 DLQ 로 격리(설계서 5.1)
        try:
            storage.put_bytes(settings.bucket_dlq, raw_key, raw_bytes)
        except Exception:
            pass
        catalog.update(task_id, status="failed", error=str(exc))
