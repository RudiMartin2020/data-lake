"""Apache Iceberg 입출력 — 카탈로그/테이블/적재/조회.

- 카탈로그: PyIceberg `SqlCatalog` (catalog_backend=postgres → PostgreSQL, 아니면 SQLite)
- warehouse: storage_backend=minio → s3://(MinIO), 아니면 file://(로컬)
- 파티션: production_date + line_id (identity) → 조회 시 파티션 프루닝
- 스키마 진화: 적재 데이터에 새 컬럼이 있으면 union_by_name 으로 무중단 추가

순수 PyArrow/PyIceberg 만 사용(DuckDB 확장 불필요) → 폐쇄망 동작.
"""
from __future__ import annotations

import threading
import time

import pyarrow as pa
from pyiceberg.catalog.sql import SqlCatalog
from pyiceberg.exceptions import (
    CommitFailedException,
    NamespaceAlreadyExistsError,
    NoSuchTableError,
    TableAlreadyExistsError,
)
from pyiceberg.expressions import And, EqualTo
from pyiceberg.transforms import IdentityTransform

from .config import settings
from .dataset import DATASET_NAME, PARTITION_KEYS

_lock = threading.Lock()
_catalog = None


def _identifier() -> str:
    return f"{settings.iceberg_namespace}.{DATASET_NAME}"


def get_catalog():
    """프로세스 단위 카탈로그 싱글턴(연결 실패 시 예외 → 설정 오류 가시화)."""
    global _catalog
    if _catalog is None:
        with _lock:
            if _catalog is None:
                settings.data_root.mkdir(parents=True, exist_ok=True)
                _catalog = SqlCatalog(
                    "lake",
                    uri=settings.iceberg_catalog_uri,
                    warehouse=settings.iceberg_warehouse,
                    **settings.iceberg_s3_props,
                )
    return _catalog


def _ensure_namespace(cat) -> None:
    try:
        cat.create_namespace(settings.iceberg_namespace)
    except (NamespaceAlreadyExistsError, Exception):
        pass


def _load_or_create(create_from: "pa.Schema | None"):
    """테이블 로드. 없으면 create_from(arrow schema)로 생성(+파티션 스펙)."""
    cat = get_catalog()
    ident = _identifier()
    try:
        return cat.load_table(ident)
    except NoSuchTableError:
        if create_from is None:
            return None
        with _lock:
            try:
                return cat.load_table(ident)
            except NoSuchTableError:
                _ensure_namespace(cat)
                try:
                    t = cat.create_table(ident, schema=create_from)
                except TableAlreadyExistsError:
                    return cat.load_table(ident)
                with t.update_spec() as us:
                    for k in PARTITION_KEYS:
                        us.add_field(k, IdentityTransform(), f"{k}_p")
                return cat.load_table(ident)


def _align_to_table(at: pa.Table, table_schema: pa.Schema) -> pa.Table:
    """arrow 테이블을 Iceberg 테이블 스키마(컬럼 순서/누락 null)로 정렬."""
    cols = {name: at.column(name) for name in at.schema.names}
    arrays = []
    for field in table_schema:
        if field.name in cols:
            arrays.append(cols[field.name].cast(field.type))
        else:
            arrays.append(pa.nulls(at.num_rows, type=field.type))
    return pa.table(arrays, schema=table_schema)


def append_arrow(at: pa.Table, retries: int = 3) -> int:
    """arrow 테이블을 Iceberg 에 적재. 새 컬럼은 스키마 진화로 추가.

    반환: 적재 데이터의 파티션 수(distinct production_date+line_id).
    """
    for attempt in range(retries):
        tbl = _load_or_create(create_from=at.schema)
        # 스키마 진화: 적재 데이터에 새 컬럼이 있으면 union(무중단 add column)
        existing = {f.name for f in tbl.schema().fields}
        if any(name not in existing for name in at.schema.names):
            with tbl.update_schema() as us:
                us.union_by_name(at.schema)
            tbl = get_catalog().load_table(_identifier())  # 진화 반영본 재로드
        aligned = _align_to_table(at, tbl.schema().as_arrow())
        try:
            tbl.append(aligned)
            break
        except CommitFailedException:
            if attempt == retries - 1:
                raise
            time.sleep(0.2 * (attempt + 1))  # 동시 커밋 충돌 → 재시도

    # 파티션 수
    keys = at.select(PARTITION_KEYS)
    return keys.group_by(PARTITION_KEYS).aggregate([]).num_rows


def scan_partition(production_date: str, line_id: str, limit: int) -> "pa.Table | None":
    """파티션 프루닝 조회. 테이블 미존재 시 None."""
    cat = get_catalog()
    try:
        tbl = cat.load_table(_identifier())
    except NoSuchTableError:
        return None
    flt = And(EqualTo("production_date", production_date), EqualTo("line_id", line_id))
    scan = tbl.scan(row_filter=flt, limit=limit)
    return scan.to_arrow()


def current_schema_fields():
    """현재 Iceberg 테이블 스키마(컬럼명, 타입). 테이블 미존재 시 None."""
    cat = get_catalog()
    try:
        tbl = cat.load_table(_identifier())
    except NoSuchTableError:
        return None
    return [(f.name, str(f.field_type)) for f in tbl.schema().fields]
