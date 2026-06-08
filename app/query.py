"""Read Path — 서빙 쿼리 엔진 (Apache Iceberg).

PyIceberg 스캔으로 파티션 프루닝(해당 production_date/line_id 만 읽기) 후
PyArrow 로 집계하여 요약 JSON 을 만든다. 임의 SQL 은 노출하지 않는다.
"""
from __future__ import annotations

import pyarrow.compute as pc

from .dataset import DATASET_NAME
from .iceberg_io import scan_partition
from .schemas import QueryRequest, QuerySummary


def _int(v) -> int:
    return int(v) if v is not None else 0


def run_query(req: QueryRequest) -> QuerySummary:
    pdate = req.production_date.isoformat()
    summary = QuerySummary(
        dataset=DATASET_NAME,
        production_date=req.production_date,
        line_id=req.line_id,
        found=False,
    )

    at = scan_partition(pdate, req.line_id, req.limit)
    if at is None or at.num_rows == 0:
        summary.note = "해당 파티션에 데이터가 없습니다."
        return summary

    summary.row_count = at.num_rows
    summary.total_qty = _int(pc.sum(at["qty"]).as_py())
    summary.total_defect_qty = _int(pc.sum(at["defect_qty"]).as_py())

    # product_id 별 집계
    grouped = at.group_by("product_id").aggregate(
        [("qty", "sum"), ("defect_qty", "sum")]
    )
    products = [
        {
            "product_id": grouped["product_id"][i].as_py(),
            "qty": _int(grouped["qty_sum"][i].as_py()),
            "defect_qty": _int(grouped["defect_qty_sum"][i].as_py()),
        }
        for i in range(grouped.num_rows)
    ]
    products.sort(key=lambda x: x["qty"], reverse=True)
    summary.products = products

    summary.found = True
    if summary.total_qty > 0:
        summary.defect_rate = round(summary.total_defect_qty / summary.total_qty, 4)
    return summary
