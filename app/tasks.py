"""작업 실행 매체 추상화.

기본: 인프로세스(ThreadPool) — Redis/Celery 없이 즉시 비동기 테스트 가능.
선택: Celery (TASK_BACKEND=celery, REDIS_URL 사용).

재시도 정책(설계서 §4.1):
  - 영구 실패(ValidationError)는 process_ingestion 내부에서 DLQ 처리(재시도 안 함)
  - 일시 실패(TransientError)는 Celery 가 self.retry 로 재시도, 소진 시 DLQ

Celery 워커 기동:
    celery -A app.tasks:celery_app worker -l info
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from . import metrics
from .config import settings
from .processing import TransientError, fail_transient, process_ingestion

_executor = ThreadPoolExecutor(max_workers=4)


def inprocess_queue_depth() -> int:
    try:
        return _executor._work_queue.qsize()  # type: ignore[attr-defined]
    except Exception:
        return 0


def _run_inprocess(task_id: str, raw_key: str, raw_bytes: bytes, dataset: str) -> None:
    """인프로세스 실행: 일시 실패는 제한적 재시도(2회) 후 DLQ."""
    for attempt in range(3):
        try:
            process_ingestion(task_id, raw_key, raw_bytes, dataset)
            return
        except TransientError as exc:
            if attempt == 2:
                fail_transient(task_id, raw_key, raw_bytes, str(exc))
                return
            metrics.INGEST_RETRY.inc()


# --- Celery (선택 백엔드) ---
celery_app = None
if settings.task_backend == "celery":
    from celery import Celery

    celery_app = Celery(
        "data_lake",
        broker=settings.redis_url,
        backend=settings.redis_url,
    )

    @celery_app.task(
        bind=True,
        name="process_ingestion",
        acks_late=True,
        max_retries=3,
        default_retry_delay=5,
    )
    def process_ingestion_task(self, task_id: str, raw_key: str, raw_bytes_hex: str, dataset: str):
        raw = bytes.fromhex(raw_bytes_hex)
        try:
            process_ingestion(task_id, raw_key, raw, dataset)
        except TransientError as exc:
            try:
                metrics.INGEST_RETRY.inc()
                raise self.retry(exc=exc)
            except self.MaxRetriesExceededError:
                fail_transient(task_id, raw_key, raw, str(exc))


def enqueue(task_id: str, raw_key: str, raw_bytes: bytes, dataset: str) -> None:
    """적재 작업을 백그라운드로 보낸다(즉시 반환)."""
    if settings.task_backend == "celery":
        if celery_app is None:  # 방어적
            raise RuntimeError("TASK_BACKEND=celery 인데 Celery 앱 초기화 실패")
        process_ingestion_task.delay(task_id, raw_key, raw_bytes.hex(), dataset)
    else:
        _executor.submit(_run_inprocess, task_id, raw_key, raw_bytes, dataset)
