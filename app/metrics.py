"""관측성 — Prometheus 메트릭 + 큐 깊이 (설계서 §8).

prometheus_client 미설치 시에도 앱이 동작하도록 no-op 으로 폴백한다.
"""
from __future__ import annotations

from .config import settings

try:
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, generate_latest

    _ENABLED = True
except Exception:  # pragma: no cover - prometheus 미설치 폴백
    _ENABLED = False
    CONTENT_TYPE_LATEST = "text/plain"

    class _Noop:
        def inc(self, *a, **k):
            pass

        def set(self, *a, **k):
            pass

        def labels(self, *a, **k):
            return self

    def Counter(*a, **k):  # type: ignore
        return _Noop()

    def Gauge(*a, **k):  # type: ignore
        return _Noop()

    def generate_latest(*a, **k):  # type: ignore
        return b"# prometheus_client not installed\n"


# --- 카운터/게이지 ---
INGEST_ACCEPTED = Counter("ingest_accepted_total", "수집 접수(202) 건수")
INGEST_DUPLICATE = Counter("ingest_duplicate_total", "중복(멱등성) 차단 건수")
INGEST_DONE = Counter("ingest_done_total", "적재 완료 건수")
INGEST_FAILED = Counter("ingest_failed_total", "적재 실패(DLQ) 건수")
INGEST_RETRY = Counter("ingest_retry_total", "적재 일시오류 재시도 건수")
QUERY_TOTAL = Counter("query_total", "서빙 쿼리 호출 건수")
QUEUE_DEPTH = Gauge("ingest_queue_depth", "대기 중 적재 작업 수")


def _update_queue_depth() -> None:
    """스크레이프 시점의 큐 깊이를 게이지에 반영."""
    try:
        if settings.task_backend == "celery":
            import redis  # 선택 의존성

            r = redis.Redis.from_url(settings.redis_url)
            QUEUE_DEPTH.set(r.llen("celery"))
        else:
            from .tasks import inprocess_queue_depth

            QUEUE_DEPTH.set(inprocess_queue_depth())
    except Exception:
        pass


def render() -> bytes:
    """/metrics 응답 본문(Prometheus 텍스트 포맷)."""
    _update_queue_depth()
    return generate_latest()
