"""헬스/정보/메트릭 엔드포인트."""
from __future__ import annotations

from fastapi import APIRouter, Response

from .. import __version__, metrics
from ..config import settings

router = APIRouter(tags=["system"])


@router.get("/metrics")
def prometheus_metrics() -> Response:
    """Prometheus 스크레이프용 메트릭(설계서 §8 관측성)."""
    return Response(content=metrics.render(), media_type=metrics.CONTENT_TYPE_LATEST)


@router.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@router.get("/info")
def info() -> dict:
    """현재 활성 백엔드 구성(폴백 여부 확인용)."""
    def _has(mod: str) -> bool:
        try:
            __import__(mod)
            return True
        except Exception:
            return False

    return {
        "app": settings.app_name,
        "version": __version__,
        "backends": {
            "storage": settings.storage_backend,
            "task": settings.task_backend,
            "catalog": settings.catalog_backend,
            "table_format": "iceberg" if _has("pyiceberg") else "n/a",
            "query_engine": "duckdb" if _has("duckdb") else "pyarrow",
        },
        "iceberg": {
            "namespace": settings.iceberg_namespace,
            "warehouse": settings.iceberg_warehouse,
        },
        "data_root": str(settings.data_root),
    }
