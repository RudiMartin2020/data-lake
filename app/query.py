"""Read Path — 서빙 쿼리 엔진.

파티션 프루닝(해당 production_date/line_id 디렉터리만 스캔) 후
집계하여 요약 JSON 을 만든다. 임의 SQL 은 노출하지 않는다.

DuckDB 가 있으면 parquet/csv 를 DuckDB 로 집계하고,
없으면 stdlib csv 로 폴백 집계한다.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional

from .config import settings
from .dataset import DATASET_NAME
from .schemas import QueryRequest, QuerySummary
from .storage import storage


def _partition_dir(production_date: str, line_id: str) -> Path:
    # 파티션 프루닝: 해당 prefix 하위만 백엔드에서 로컬로 확보(MinIO) / 로컬 경로(local)
    prefix = f"{DATASET_NAME}/data/production_date={production_date}/line_id={line_id}"
    return storage.materialize_prefix(settings.bucket_warehouse, prefix)


def run_query(req: QueryRequest) -> QuerySummary:
    pdate = req.production_date.isoformat()
    part_dir = _partition_dir(pdate, req.line_id)

    summary = QuerySummary(
        dataset=DATASET_NAME,
        production_date=req.production_date,
        line_id=req.line_id,
        found=False,
    )
    if not part_dir.exists():
        summary.note = "해당 파티션에 데이터가 없습니다."
        return summary

    parquet_files = list(part_dir.glob("*.parquet"))
    csv_files = list(part_dir.glob("*.csv"))

    if parquet_files and _duckdb_available():
        _aggregate_duckdb(parquet_files, summary, req.limit)
    elif csv_files and _duckdb_available():
        _aggregate_duckdb(csv_files, summary, req.limit)
    elif csv_files:
        _aggregate_stdlib(csv_files, summary, req.limit)
    else:
        summary.note = "데이터 파일 형식을 읽을 수 없습니다."
        return summary

    summary.found = summary.row_count > 0
    if summary.total_qty > 0:
        summary.defect_rate = round(summary.total_defect_qty / summary.total_qty, 4)
    return summary


def _duckdb_available() -> bool:
    try:
        import duckdb  # noqa: F401
        return True
    except Exception:
        return False


def _aggregate_duckdb(files: List[Path], summary: QuerySummary, limit: int) -> None:
    import duckdb

    paths = [f.as_posix() for f in files]
    con = duckdb.connect()
    try:
        is_parquet = files[0].suffix == ".parquet"
        reader = "read_parquet" if is_parquet else "read_csv_auto"
        src = f"{reader}({paths!r})"
        # 안전장치: limit 로 스캔 행 상한
        rows = con.execute(
            f"SELECT product_id, "
            f"SUM(CAST(qty AS BIGINT)) qty, SUM(CAST(defect_qty AS BIGINT)) defect "
            f"FROM (SELECT * FROM {src} LIMIT {int(limit)}) GROUP BY product_id ORDER BY qty DESC"
        ).fetchall()
        total = con.execute(
            f"SELECT COUNT(*), COALESCE(SUM(CAST(qty AS BIGINT)),0), "
            f"COALESCE(SUM(CAST(defect_qty AS BIGINT)),0) "
            f"FROM (SELECT * FROM {src} LIMIT {int(limit)})"
        ).fetchone()
    finally:
        con.close()

    summary.row_count = int(total[0])
    summary.total_qty = int(total[1])
    summary.total_defect_qty = int(total[2])
    summary.products = [
        {"product_id": r[0], "qty": int(r[1]), "defect_qty": int(r[2])} for r in rows
    ]


def _aggregate_stdlib(files: List[Path], summary: QuerySummary, limit: int) -> None:
    by_product: dict = {}
    count = 0
    for f in files:
        with f.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if count >= limit:
                    break
                count += 1
                pid = row.get("product_id", "")
                qty = _to_int(row.get("qty"))
                dq = _to_int(row.get("defect_qty"))
                agg = by_product.setdefault(pid, {"product_id": pid, "qty": 0, "defect_qty": 0})
                agg["qty"] += qty
                agg["defect_qty"] += dq
                summary.total_qty += qty
                summary.total_defect_qty += dq
    summary.row_count = count
    summary.products = sorted(by_product.values(), key=lambda x: x["qty"], reverse=True)


def _to_int(v: Optional[str]) -> int:
    try:
        return int(float(v)) if v not in (None, "") else 0
    except (ValueError, TypeError):
        return 0
