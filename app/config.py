"""환경설정.

모든 외부 백엔드(MinIO/Redis/Celery/PostgreSQL)는 *선택*이며,
설정하지 않으면 단일 프로세스에서 바로 테스트 가능한 개발용 폴백을 사용한다.

  - STORAGE_BACKEND : "local"(기본) | "minio"
  - TASK_BACKEND    : "inprocess"(기본) | "celery"
  - CATALOG_BACKEND : "sqlite"(기본) | "postgres"
  - QUERY_ENGINE    : "duckdb"(설치 시 자동) | "stdlib"(폴백)
"""
from __future__ import annotations

import os
from pathlib import Path


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


class Settings:
    # --- 일반 ---
    app_name: str = "RPA Data-to-AI"
    # 데이터 루트(로컬 FS 백엔드 및 SQLite 카탈로그 위치)
    data_root: Path = Path(_env("DATA_ROOT", str(Path(__file__).resolve().parent.parent / "data")))

    # MinIO 버킷에 해당하는 논리 디렉터리 이름
    bucket_raw: str = "raw"
    bucket_staging: str = "staging"
    bucket_warehouse: str = "warehouse"
    bucket_dlq: str = "dlq"

    # --- 백엔드 선택 ---
    storage_backend: str = _env("STORAGE_BACKEND", "local")
    task_backend: str = _env("TASK_BACKEND", "inprocess")
    catalog_backend: str = _env("CATALOG_BACKEND", "sqlite")

    # --- MinIO (storage_backend == "minio") ---
    minio_endpoint: str = _env("MINIO_ENDPOINT", "localhost:9000")
    minio_access_key: str = _env("MINIO_ACCESS_KEY", "minioadmin")
    minio_secret_key: str = _env("MINIO_SECRET_KEY", "minioadmin")
    minio_secure: bool = _env("MINIO_SECURE", "false").lower() == "true"

    # --- Redis / Celery (task_backend == "celery") ---
    redis_url: str = _env("REDIS_URL", "redis://localhost:6379/0")

    # --- PostgreSQL (catalog_backend == "postgres") ---
    # 예: postgresql://flopi_adm:****@localhost:5432/flopi
    postgres_dsn: str = _env("POSTGRES_DSN", "")

    # --- 조회 안전장치 ---
    query_row_limit: int = int(_env("QUERY_ROW_LIMIT", "10000"))

    @property
    def raw_dir(self) -> Path:
        return self.data_root / self.bucket_raw

    @property
    def staging_dir(self) -> Path:
        return self.data_root / self.bucket_staging

    @property
    def warehouse_dir(self) -> Path:
        return self.data_root / self.bucket_warehouse

    @property
    def dlq_dir(self) -> Path:
        return self.data_root / self.bucket_dlq

    @property
    def sqlite_path(self) -> Path:
        return self.data_root / "catalog.db"


settings = Settings()
