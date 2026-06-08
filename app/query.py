"""Read Path — 서빙 쿼리 엔진 (Iceberg 프루닝 + DuckDB in-memory 집계, 멀티 데이터셋).

  1) PyIceberg 가 파티션 프루닝(filters)으로 PyArrow 테이블을 읽고
  2) DuckDB 가 그 PyArrow 테이블을 in-memory 로 SQL 집계한다(제로카피).

집계 대상(measures)·그룹 기준(group_by)은 dataset 레지스트리에서 가져온다.
DuckDB iceberg/httpfs 확장이 필요 없으므로 폐쇄망에서 동작한다.
"""
from __future__ import annotations

import duckdb

from . import metrics
from .dataset import get_dataset
from .iceberg_io import scan_partition
from .schemas import QueryRequest, QuerySummary


def _agg_expr(ds, alias_prefix: str = "") -> str:
    parts = []
    for m in ds.measures:
        cast = "BIGINT" if ds.is_integer_measure(m) else "DOUBLE"
        parts.append(f"SUM(CAST({m} AS {cast})) AS {m}")
    return ", ".join(parts)


def run_query(req: QueryRequest) -> QuerySummary:
    metrics.QUERY_TOTAL.inc()
    ds = get_dataset(req.dataset)
    summary = QuerySummary(dataset=req.dataset, filters=req.filters, found=False)

    # 1) 파티션 프루닝 → PyArrow
    part = scan_partition(req.dataset, req.filters, req.limit)
    if part is None or part.num_rows == 0:
        summary.note = "해당 파티션에 데이터가 없습니다."
        return summary

    # 2) DuckDB in-memory 집계
    con = duckdb.connect()
    try:
        con.register("part", part)
        agg = _agg_expr(ds)
        totals_row = con.execute(f"SELECT COUNT(*), {agg} FROM part").fetchone()
        groups = con.execute(
            f"SELECT {ds.group_by}, {agg} FROM part GROUP BY {ds.group_by} "
            f"ORDER BY {ds.measures[0]} DESC"
        ).fetchall()
        group_cols = [d[0] for d in con.description]
    finally:
        con.close()

    summary.row_count = int(totals_row[0])
    summary.totals = {m: _num(totals_row[i + 1], ds.is_integer_measure(m))
                      for i, m in enumerate(ds.measures)}
    summary.groups = [
        {col: _maybe_num(col, val, ds) for col, val in zip(group_cols, row)}
        for row in groups
    ]
    summary.found = summary.row_count > 0
    return summary


def _num(v, is_int: bool):
    if v is None:
        return 0 if is_int else 0.0
    return int(v) if is_int else float(v)


def _maybe_num(col: str, val, ds):
    if col in ds.measures:
        return _num(val, ds.is_integer_measure(col))
    return val
