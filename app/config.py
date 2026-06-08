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

    # --- PostgreSQL (catalog_backend == "postgres") — 운영 표준 분리형 변수명 ---
    # 우선순위: POSTGRES_DSN(단일) > PG_* (분리형)
    pg_host: str = _env("PG_HOST", "")
    pg_port: str = _env("PG_PORT", "5432")
    pg_user: str = _env("PG_USER", "")
    pg_password: str = _env("PG_PASSWORD", "")
    pg_db: str = _env("PG_DB", "")
    _postgres_dsn_explicit: str = _env("POSTGRES_DSN", "")

    @property
    def postgres_dsn(self) -> str:
        if self._postgres_dsn_explicit:
            return self._postgres_dsn_explicit
        if self.pg_host and self.pg_user:
            return (
                f"postgresql://{self.pg_user}:{self.pg_password}"
                f"@{self.pg_host}:{self.pg_port}/{self.pg_db}"
            )
        return ""

    # --- 조회 안전장치 ---
    query_row_limit: int = int(_env("QUERY_ROW_LIMIT", "10000"))

    # --- Apache Iceberg (테이블 포맷) ---
    # 카탈로그: catalog_backend(postgres|sqlite) 재사용. warehouse: storage_backend(minio|local)에 따라 s3://|file://
    iceberg_namespace: str = _env("ICEBERG_NAMESPACE", "lake")

    @property
    def iceberg_catalog_uri(self) -> str:
        """PyIceberg SqlCatalog 용 SQLAlchemy URI."""
        if self.catalog_backend == "postgres":
            dsn = self.postgres_dsn
            if dsn.startswith("postgresql://"):
                dsn = "postgresql+psycopg2://" + dsn[len("postgresql://"):]
            return dsn
        # 폴백: SQLite 카탈로그(파일)
        return f"sqlite:///{(self.data_root / 'iceberg_catalog.db').as_posix()}"

    @property
    def iceberg_warehouse(self) -> str:
        if self.storage_backend == "minio":
            return f"s3://{self.bucket_warehouse}/iceberg"
        return f"file://{(self.data_root / 'iceberg').as_posix()}"

    @property
    def iceberg_s3_props(self) -> dict:
        if self.storage_backend != "minio":
            return {}
        scheme = "https" if self.minio_secure else "http"
        return {
            "s3.endpoint": f"{scheme}://{self.minio_endpoint}",
            "s3.access-key-id": self.minio_access_key,
            "s3.secret-access-key": self.minio_secret_key,
            "s3.region": "us-east-1",
            "s3.path-style-access": "true",
        }

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
