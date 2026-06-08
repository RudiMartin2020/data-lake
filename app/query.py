"""Read Path — 서빙 쿼리 엔진 (Iceberg 프루닝 + DuckDB in-memory 집계).

하이브리드 설계:
  1) PyIceberg 가 파티션 프루닝(해당 production_date/line_id 만)으로 PyArrow 테이블을 읽고
  2) DuckDB 가 그 PyArrow 테이블을 in-memory 로 SQL 집계한다(제로카피).

DuckDB iceberg/httpfs 확장이 필요 없으므로 폐쇄망(C-1)에서 동작하며,
요구 스택의 "DuckDB(in-memory 기반 파티션 조회)"를 충족한다.
임의 SQL 은 노출하지 않는다(고정 집계 쿼리).
"""
from __future__ import annotations

import duckdb

from . import metrics
from .dataset import DATASET_NAME
from .iceberg_io import scan_partition
from .schemas import QueryRequest, QuerySummary

_TOTALS_SQL = (
    "SELECT COUNT(*), "
    "COALESCE(SUM(CAST(qty AS BIGINT)), 0), "
    "COALESCE(SUM(CAST(defect_qty AS BIGINT)), 0) FROM part"
)
_PRODUCTS_SQL = (
    "SELECT product_id, "
    "SUM(CAST(qty AS BIGINT)) AS qty, "
    "SUM(CAST(defect_qty AS BIGINT)) AS defect_qty "
    "FROM part GROUP BY product_id ORDER BY qty DESC"
)


def run_query(req: QueryRequest) -> QuerySummary:
    metrics.QUERY_TOTAL.inc()
    pdate = req.production_date.isoformat()
    summary = QuerySummary(
        dataset=DATASET_NAME,
        production_date=req.production_date,
        line_id=req.line_id,
        found=False,
    )

    # 1) PyIceberg 파티션 프루닝 → PyArrow (S3/메타데이터는 PyIceberg 가 처리)
    part = scan_partition(pdate, req.line_id, req.limit)
    if part is None or part.num_rows == 0:
        summary.note = "해당 파티션에 데이터가 없습니다."
        return summary

    # 2) DuckDB in-memory 집계 (PyArrow 제로카피 등록 — 확장 불필요)
    con = duckdb.connect()
    try:
        con.register("part", part)
        row_count, total_qty, total_defect = con.execute(_TOTALS_SQL).fetchone()
        products = con.execute(_PRODUCTS_SQL).fetchall()
    finally:
        con.close()

    summary.row_count = int(row_count)
    summary.total_qty = int(total_qty)
    summary.total_defect_qty = int(total_defect)
    summary.products = [
        {"product_id": p[0], "qty": int(p[1]), "defect_qty": int(p[2])} for p in products
    ]
    summary.found = summary.row_count > 0
    if summary.total_qty > 0:
        summary.defect_rate = round(summary.total_defect_qty / summary.total_qty, 4)
    return summary
