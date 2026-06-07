"""작업 실행 매체 추상화.

기본: 인프로세스(ThreadPool) — Redis/Celery 없이 즉시 비동기 테스트 가능.
선택: Celery (TASK_BACKEND=celery, REDIS_URL 사용).

Celery 워커 기동:
    celery -A app.tasks:celery_app worker -l info

두 경우 모두 실제 처리 로직은 processing.process_ingestion 이다.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .config import settings
from .processing import process_ingestion

_executor = ThreadPoolExecutor(max_workers=4)

# --- Celery (선택 백엔드) — 모듈 레벨 앱이라야 `celery -A app.tasks:celery_app` 가 찾는다 ---
celery_app = None
if settings.task_backend == "celery":
    from celery import Celery

    celery_app = Celery(
        "data_lake",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )

    @celery_app.task(name="process_ingestion", acks_late=True, max_retries=3)
    def process_ingestion_task(task_id: str, raw_key: str, raw_bytes_hex: str):
        process_ingestion(task_id, raw_key, bytes.fromhex(raw_bytes_hex))


def enqueue(task_id: str, raw_key: str, raw_bytes: bytes) -> None:
    """적재 작업을 백그라운드로 보낸다(즉시 반환)."""
    if settings.task_backend == "celery":
        if celery_app is None:  # 방어적
            raise RuntimeError("TASK_BACKEND=celery 인데 Celery 앱 초기화 실패")
        process_ingestion_task.delay(task_id, raw_key, raw_bytes.hex())
    else:
        _executor.submit(process_ingestion, task_id, raw_key, raw_bytes)
