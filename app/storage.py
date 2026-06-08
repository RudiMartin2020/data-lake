"""오브젝트 스토리지 추상화 (원본 raw/ · DLQ dlq/ 보관, Iceberg warehouse 버킷 보장).

기본: 로컬 파일시스템(개발/테스트). 선택: MinIO (STORAGE_BACKEND=minio).

warehouse 의 실제 데이터/메타데이터는 PyIceberg(iceberg_io)가 직접 관리한다.
이 모듈은 다음만 담당한다.
  - put_bytes(bucket, key, data) : 원본/실패본 저장
  - get_bytes(bucket, key)       : 재처리용 원본 읽기
  - 버킷(raw/warehouse/dlq) 보장
"""
from __future__ import annotations

from .config import settings

# warehouse: Iceberg S3 warehouse 가 존재해야 하므로 보장. staging 은 미사용(제거).
_BUCKETS = (settings.bucket_raw, settings.bucket_warehouse, settings.bucket_dlq)


class LocalStorage:
    """data_root 하위 디렉터리를 버킷처럼 사용."""

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

    def get_bytes(self, bucket: str, key: str) -> bytes:
        return (self.root / bucket / key).read_bytes()


class MinioStorage:
    """MinIO(S3) 백엔드."""

    backend = "minio"

    def __init__(self) -> None:
        from minio import Minio  # 선택 의존성

        self.client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=settings.minio_secure,
        )
        # 버킷 보장(연결 불가 시 여기서 예외 → 설정 오류를 조용히 숨기지 않음)
        for bucket in _BUCKETS:
            if not self.client.bucket_exists(bucket):
                self.client.make_bucket(bucket)

    def put_bytes(self, bucket: str, key: str, data: bytes) -> str:
        import io

        self.client.put_object(bucket, key, io.BytesIO(data), length=len(data))
        return f"s3://{bucket}/{key}"

    def get_bytes(self, bucket: str, key: str) -> bytes:
        resp = self.client.get_object(bucket, key)
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()


def get_storage():
    if settings.storage_backend == "minio":
        return MinioStorage()
    if settings.storage_backend == "local":
        return LocalStorage()
    raise ValueError(f"알 수 없는 STORAGE_BACKEND: {settings.storage_backend}")


# 모듈 수준 싱글턴
storage = get_storage()
