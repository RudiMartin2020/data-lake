"""적재 처리: parse → validate → Iceberg 적재 → 카탈로그 기록.

오류를 두 종류로 구분한다(설계서 §4.1, §8):
  - ValidationError(영구): 파싱·필수컬럼 오류 → 재시도 무의미 → DLQ + failed
  - TransientError(일시): 적재(MinIO/PG/Iceberg) 인프라 오류 → 호출자(Celery)가 재시도

process_ingestion 은 ValidationError 를 내부 처리(DLQ)하고, TransientError 는 raise 한다.
"""
from __future__ import annotations

import io
import logging
from typing import List

import pyarrow as pa
import pyarrow.csv as pacsv

from . import metrics
from .audit import audit
from .config import settings
from .dataset import get_dataset
from .iceberg_io import append_arrow
from .storage import storage

logger = logging.getLogger("data_lake.processing")


class ValidationError(Exception):
    """영구 실패(재시도 안 함)."""


class TransientError(Exception):
    """일시 실패(재시도 대상)."""


def _read_csv(data: bytes) -> pa.Table:
    return pacsv.read_csv(io.BytesIO(data))


def _validate(dataset: str, names: List[str]) -> None:
    missing = [c for c in get_dataset(dataset).required if c not in names]
    if missing:
        raise ValidationError(f"필수 컬럼 누락: {missing} (헤더={names})")


def _to_dlq(task_id: str, raw_key: str, raw_bytes: bytes, error: str) -> None:
    try:
        storage.put_bytes(settings.bucket_dlq, raw_key, raw_bytes)
    except Exception:
        logger.exception("DLQ 저장 실패 task=%s", task_id)
    audit.update(task_id, status="failed", error=error)
    metrics.INGEST_FAILED.inc()
    logger.warning("적재 실패(DLQ) task=%s: %s", task_id, error)


def process_ingestion(task_id: str, raw_key: str, raw_bytes: bytes, dataset: str) -> None:
    """단일 적재 작업.

    - 파싱/검증 실패 → DLQ + failed (반환, 재시도 안 함)
    - 적재 인프라 실패 → TransientError raise (호출자가 재시도)
    """
    audit.update(task_id, status="processing")

    # 1) 파싱·검증 (영구 실패 영역)
    try:
        at = _read_csv(raw_bytes)
        _validate(dataset, at.schema.names)
    except Exception as exc:  # noqa: BLE001 — 영구 실패 → DLQ
        _to_dlq(task_id, raw_key, raw_bytes, f"validation: {exc}")
        return

    # 2) 적재 (일시 실패 영역 → 재시도 대상)
    try:
        partitions = append_arrow(dataset, at)
    except Exception as exc:  # noqa: BLE001
        logger.warning("적재 일시 오류(재시도 대상) task=%s: %s", task_id, exc)
        raise TransientError(str(exc)) from exc

    audit.update(task_id, status="done", rows=at.num_rows, partitions=partitions)
    metrics.INGEST_DONE.inc()
    logger.info("적재 완료 task=%s dataset=%s rows=%d partitions=%d",
                task_id, dataset, at.num_rows, partitions)


def fail_transient(task_id: str, raw_key: str, raw_bytes: bytes, error: str) -> None:
    """일시 실패 재시도 소진 시 호출 — DLQ 격리 + failed 기록."""
    _to_dlq(task_id, raw_key, raw_bytes, f"transient(소진): {error}")
