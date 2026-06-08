"""스토리지 추상화.

기본: 로컬 파일시스템(개발/테스트 — 추가 서비스 불필요)
선택: MinIO (STORAGE_BACKEND=minio)

논리 버킷(raw/staging/warehouse/dlq)은 두 백엔드에서 동일하게 노출된다.
DuckDB 는 로컬 파일만 읽으므로, MinIO 백엔드는 다음 규약으로 로컬과 동기화한다.

  - put_bytes(bucket, key, data)        : 바이트를 백엔드에 저장
  - local_write_path(bucket, key)       : 파일을 *로컬에* 쓸 경로 반환(쓰기 전용 준비)
  - commit(bucket, key)                 : local_write_path 에 쓴 파일을 백엔드로 발행
  - materialize_prefix(bucket, prefix)  : prefix 하위 파일을 로컬에 확보하고 그 디렉터리 반환
  - exists(bucket, key)                 : 존재 여부
"""
from __future__ import annotations

from pathlib import Path

from .config import settings

_BUCKETS = (
    settings.bucket_raw,
    settings.bucket_staging,
    settings.bucket_warehouse,
    settings.bucket_dlq,
)


class LocalStorage:
    """data_root 하위 디렉터리를 버킷처럼 사용. local_write_path == 실제 경로."""

    backend = "local"

    def __init__(self) -> None:
        self.root = settings.data_root
        for bucket in _BUCKETS:
            (self.root / bucket).mkdir(parents=True, exist_ok=True)

    def put_bytes(self, bucket: str, key: str, data: bytes) -> str:
        path = self.root / bucket / key
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)

    def local_write_path(self, bucket: str, key: str) -> Path:
        path = self.root / bucket / key
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def bucket_root(self, bucket: str) -> Path:
        return self.root / bucket

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return (self.root / bucket / key).read_bytes()

    def commit(self, bucket: str, key: str) -> None:  # 로컬은 이미 제자리
        return None

    def materialize_prefix(self, bucket: str, prefix: str) -> Path:
        return self.root / bucket / prefix

    def exists(self, bucket: str, key: str) -> bool:
        return (self.root / bucket / key).exists()


class MinioStorage:
    """MinIO(S3) 백엔드.

    쓰기: 로컬 캐시에 쓴 뒤 commit() 에서 업로드.
    읽기: materialize_prefix() 가 prefix 하위 객체를 캐시로 내려받아 로컬 경로 제공.
    """

    backend = "minio"

    def __init__(self) -> None:
        from minio import Minio  # 선택 의존성

        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        self.cache_root = settings.data_root / "_minio_cache"
        # 버킷 보장(연결 불가 시 여기서 예외 발생 → 설정 오류를 조용히 숨기지 않음)
        for bucket in _BUCKETS:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    def _cache(self, bucket: str, key: str) -> Path:
        return self.cache_root / bucket / key

    def put_bytes(self, bucket: str, key: str, data: bytes) -> str:
        import io
        self.client.put_object(bucket, key, io.BytesIO(data), length=len(data))
        # 캐시에도 반영(이후 로컬 읽기 일관성)
        c = self._cache(bucket, key)
        c.parent.mkdir(parents=True, exist_ok=True)
        c.write_bytes(data)
        return f"s3://{bucket}/{key}"

    def local_write_path(self, bucket: str, key: str) -> Path:
        path = self._cache(bucket, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    def bucket_root(self, bucket: str) -> Path:
        return self.cache_root / bucket

    def get_bytes(self, bucket: str, key: str) -> bytes:
        resp = self.client.get_object(bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    def commit(self, bucket: str, key: str) -> None:
        local = self._cache(bucket, key)
        self.client.fput_object(bucket, key, str(local))

    def materialize_prefix(self, bucket: str, prefix: str) -> Path:
        target_dir = self._cache(bucket, prefix)
        # prefix 하위 객체를 모두 내려받아 캐시 디렉터리 구성
        for obj in self.client.list_objects(bucket, prefix=prefix, recursive=True):
            dest = self._cache(bucket, obj.object_name)
            dest.parent.mkdir(parents=True, exist_ok=True)
            self.client.fget_object(bucket, obj.object_name, str(dest))
        return target_dir

    def exists(self, bucket: str, key: str) -> bool:
        try:
            self.client.stat_object(bucket, key)
            return True
        except Exception:
            return False


def get_storage():
    if settings.storage_backend == "minio":
        return MinioStorage()
    if settings.storage_backend == "local":
        return LocalStorage()
    raise ValueError(f"알 수 없는 STORAGE_BACKEND: {settings.storage_backend}")


# 모듈 수준 싱글턴
storage = get_storage()
