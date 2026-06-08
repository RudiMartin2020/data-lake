"""REQ-01 — 비동기 원천 데이터 수집(Ingestion)."""
from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from .. import metrics
from ..auth import verify_ingest
from ..audit import audit
from ..config import settings
from ..dataset import DATASETS, DEFAULT_DATASET
from ..schemas import IngestAccepted, TaskStatus
from ..storage import storage
from ..tasks import enqueue

router = APIRouter(tags=["ingestion"], dependencies=[Depends(verify_ingest)])


@router.post("/ingest", response_model=IngestAccepted, status_code=status.HTTP_202_ACCEPTED)
async def ingest(
    file: UploadFile = File(...),
    source_id: str = Form(...),
    dataset: str = Form(DEFAULT_DATASET),
) -> IngestAccepted:
    """파일을 받아 즉시 202 응답 후, 백그라운드 워커가 적재 처리한다."""
    if dataset not in DATASETS:
        raise HTTPException(status_code=400, detail=f"알 수 없는 dataset: {dataset} (가능: {list(DATASETS)})")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="빈 파일입니다.")

    content_hash = hashlib.sha256(raw).hexdigest()

    # 멱등성: 동일 content_hash 중복 적재 방지(설계서 4.1)
    dup = audit.find_by_hash(content_hash)
    if dup:
        metrics.INGEST_DUPLICATE.inc()
        return IngestAccepted(
            task_id=dup["task_id"],
            status="duplicate",
            dataset=dup.get("dataset") or dataset,
            source_id=source_id,
            filename=file.filename or "",
            content_hash=content_hash,
            duplicate=True,
        )

    task_id = uuid.uuid4().hex
    raw_key = f"{task_id}__{file.filename or 'upload.csv'}"

    # 원본 보존(raw/) — 감사·재처리
    storage.put_bytes(settings.bucket_raw, raw_key, raw)
    audit.create(
        task_id=task_id,
        dataset=dataset,
        source_id=source_id,
        content_hash=content_hash,
        filename=file.filename or "",
    )

    # 비동기 처리 큐잉(인프로세스 또는 Celery)
    enqueue(task_id, raw_key, raw, dataset)
    metrics.INGEST_ACCEPTED.inc()

    return IngestAccepted(
        task_id=task_id,
        status="accepted",
        dataset=dataset,
        source_id=source_id,
        filename=file.filename or "",
        content_hash=content_hash,
    )


@router.get("/ingest/status/{task_id}", response_model=TaskStatus)
def ingest_status(task_id: str) -> TaskStatus:
    rec = audit.get(task_id)
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


@router.post("/ingest/reprocess/{task_id}", response_model=IngestAccepted,
             status_code=status.HTTP_202_ACCEPTED)
def reprocess(task_id: str) -> IngestAccepted:
    """실패(DLQ) 건을 보존된 원본(raw/)에서 재적재한다(설계서 §8 재처리 경로)."""
    rec = audit.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task_id 를 찾을 수 없습니다.")
    if rec["status"] != "failed":
        raise HTTPException(status_code=409, detail=f"failed 상태만 재처리 가능(현재: {rec['status']}).")

    raw_key = f"{task_id}__{rec.get('filename') or 'upload.csv'}"
    try:
        raw = storage.get_bytes(settings.bucket_raw, raw_key)
    except Exception:
        raise HTTPException(status_code=410, detail="원본(raw) 보존본을 찾을 수 없습니다.")

    ds = rec.get("dataset") or DEFAULT_DATASET
    audit.update(task_id, status="accepted", error=None)
    enqueue(task_id, raw_key, raw, ds)
    metrics.INGEST_ACCEPTED.inc()
    return IngestAccepted(
        task_id=task_id,
        status="reprocessing",
        dataset=ds,
        source_id=rec.get("source_id") or "",
        filename=rec.get("filename") or "",
        content_hash=rec.get("content_hash") or "",
    )
