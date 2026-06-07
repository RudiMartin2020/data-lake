"""헬스/정보 엔드포인트."""
from __future__ import annotations

from fastapi import APIRouter

from .. import __version__
from ..config import settings

router = APIRouter(tags=["system"])


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
            "query_engine": "duckdb" if _has("duckdb") else "stdlib",
        },
        "data_root": str(settings.data_root),
    }
