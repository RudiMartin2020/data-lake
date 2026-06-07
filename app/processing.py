"""적재 처리: parse → validate → 파티션 변환 → warehouse 적재 → 카탈로그 기록.

Write Path 의 워커 본체. 동기 함수로 구현하고, 실행 매체(인프로세스/Celery)는
tasks.py 가 선택한다.

DuckDB 가 있으면 CSV → Parquet(파티션) 로 변환하고, 없으면 CSV 를 그대로
파티션 경로에 기록한다(폴백). 스토리지 백엔드(local/minio)는 storage 추상화가 처리한다.
"""
from __future__ import annotations

import csv
import io
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, List, Tuple

from .catalog import catalog
from .config import settings
from .dataset import DATASET_NAME, PARTITION_KEYS, REQUIRED_COLUMNS
from .storage import storage


class ValidationError(Exception):
    pass


def _read_csv(data: bytes) -> Tuple[List[str], List[Dict[str, str]]]:
    text = data.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    header = reader.fieldnames or []
    return header, rows


def _validate(header: List[str]) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in header]
    if missing:
        raise ValidationError(f"필수 컬럼 누락: {missing} (헤더={header})")


def _partition_key(row: Dict[str, str]) -> str:
    return "/".join(f"{k}={row[k]}" for k in PARTITION_KEYS)


def _warehouse_key(part: str, filename: str) -> str:
    return f"{DATASET_NAME}/data/{part}/{filename}"


def _try_duckdb_write(task_id: str, staging_path):
    """DuckDB 단일 패스로 전체 파티션을 한 번에 기록(staging CSV 1회 스캔).

    `COPY ... PARTITION_BY` 로 Hive 파티션 트리를 생성하고, APPEND 로 기존 파티션에
    누적(증분 적재)한다. 파일명은 `<task_id>_<uuid>` 로 작업 간 충돌을 방지한다.
    백엔드(MinIO) 업로드는 병렬로 수행한다.

    반환: 이번 적재가 만든 **파티션 수**(int). DuckDB 미설치 시 None → CSV 폴백.
    """
    try:
        import duckdb  # 선택 의존성
    except Exception:
        return None

    out_dir = storage.local_write_path(settings.bucket_warehouse, f"{DATASET_NAME}/data")
    out_dir.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect()
    try:
        con.execute(
            f"COPY (SELECT * FROM read_csv_auto('{staging_path.as_posix()}')) "
            f"TO '{out_dir.as_posix()}' "
            f"(FORMAT PARQUET, PARTITION_BY (production_date, line_id), "
            f"APPEND, FILENAME_PATTERN '{task_id}_{{uuid}}')"
        )
    finally:
        con.close()

    # 백엔드 발행: 이번 적재가 만든 parquet 만 업로드(MinIO). local 은 commit no-op.
    files = list(out_dir.rglob(f"{task_id}_*.parquet"))
    root = storage.bucket_root(settings.bucket_warehouse)

    def _publish(f):
        storage.commit(settings.bucket_warehouse, f.relative_to(root).as_posix())

    if files:
        with ThreadPoolExecutor(max_workers=16) as ex:
            list(ex.map(_publish, files))

    return len({f.parent for f in files})


def _csv_write(rows_by_part: Dict[str, List[Dict[str, str]]], header: List[str]) -> None:
    for part, rows in rows_by_part.items():
        key = _warehouse_key(part, "data.csv")
        out_file = storage.local_write_path(settings.bucket_warehouse, key)
        with out_file.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=header)
            writer.writeheader()
            writer.writerows(rows)
        storage.commit(settings.bucket_warehouse, key)


def process_ingestion(task_id: str, raw_key: str, raw_bytes: bytes) -> None:
    """단일 적재 작업 처리. 예외는 잡아 카탈로그에 failed/DLQ 로 기록."""
    catalog.update(task_id, status="processing")
    try:
        header, rows = _read_csv(raw_bytes)
        _validate(header)

        # staging 사본(DuckDB 가 읽을 로컬 파일 + 백엔드 보존)
        staging_key = f"{task_id}.csv"
        staging_path = storage.local_write_path(settings.bucket_staging, staging_key)
        staging_path.write_bytes(raw_bytes)
        storage.commit(settings.bucket_staging, staging_key)

        # DuckDB 단일 패스 적재(우선). 미설치 시에만 파티션 분할 후 CSV 폴백.
        partitions = _try_duckdb_write(task_id, staging_path)
        if partitions is None:
            rows_by_part: Dict[str, List[Dict[str, str]]] = defaultdict(list)
            for r in rows:
                rows_by_part[_partition_key(r)].append(r)
            _csv_write(rows_by_part, header)
            partitions = len(rows_by_part)

        catalog.update(
            task_id,
            status="done",
            rows=len(rows),
            partitions=partitions,
        )
    except Exception as exc:  # noqa: BLE001 — DLQ 격리 목적
        # 파싱 실패 건은 DLQ 로 격리(설계서 5.1)
        try:
            storage.put_bytes(settings.bucket_dlq, raw_key, raw_bytes)
        except Exception:
            pass
        catalog.update(task_id, status="failed", error=str(exc))
