"""REQ-01 — 비동기 원천 데이터 수집(Ingestion)."""
from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from ..catalog import catalog
from ..config import settings
from ..dataset import DATASET_NAME
from ..schemas import IngestAccepted, TaskStatus
from ..storage import storage
from ..tasks import enqueue

router = APIRouter(tags=["ingestion"])


@router.post("/ingest", response_model=IngestAccepted, status_code=status.HTTP_202_ACCEPTED)
async def ingest(file: UploadFile = File(...), source_id: str = Form(...)) -> IngestAccepted:
    """파일을 받아 즉시 202 응답 후, 백그라운드 워커가 적재 처리한다."""
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    content_hash = hashlib.sha256(raw).hexdigest()

    # 멱등성: 동일 content_hash 중복 적재 방지(설계서 4.1)
    dup = catalog.find_by_hash(content_hash)
    if dup:
        return IngestAccepted(
            task_id=dup["task_id"],
            status="duplicate",
            source_id=source_id,
            filename=file.filename or "",
            content_hash=content_hash,
            duplicate=True,
        )

    task_id = uuid.uuid4().hex
    raw_key = f"{task_id}__{file.filename or 'upload.csv'}"

    # 원본 보존(raw/) — 감사·재처리
    storage.put_bytes(settings.bucket_raw, raw_key, raw)
    catalog.create(
        task_id=task_id,
        dataset=DATASET_NAME,
        source_id=source_id,
        content_hash=content_hash,
        filename=file.filename or "",
    )

    # 비동기 처리 큐잉(인프로세스 또는 Celery)
    enqueue(task_id, raw_key, raw)

    return IngestAccepted(
        task_id=task_id,
        status="accepted",
        source_id=source_id,
        filename=file.filename or "",
        content_hash=content_hash,
    )


@router.get("/ingest/status/{task_id}", response_model=TaskStatus)
def ingest_status(task_id: str) -> TaskStatus:
    rec = catalog.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task_id 를 찾을 수 없습니다.")
    return TaskStatus(
        task_id=rec["task_id"],
        status=rec["status"],
        dataset=rec.get("dataset"),
        rows=rec.get("rows"),
        partitions=rec.get("partitions"),
        error=rec.get("error"),
    )
