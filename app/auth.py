"""API 인증 — 수집/서빙 분리 서비스 토큰 (설계서 §7).

헤더 `X-Service-Token` 값을 설정된 토큰과 대조한다.
토큰이 설정되지 않은 경우("") 해당 경로의 인증을 **비활성**한다(개발 폴백).
운영에서는 INGEST_TOKEN / SERVING_TOKEN 을 반드시 설정한다.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, status

from .config import settings

_HEADER = "X-Service-Token"


def _check(provided: Optional[str], expected: str, scope: str) -> None:
    if not expected:
        return  # 토큰 미설정 → 인증 비활성(개발)
    if not provided or provided != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"{scope} 인증 실패: 유효한 {_HEADER} 가 필요합니다.",
            headers={"WWW-Authenticate": _HEADER},
        )


def verify_ingest(x_service_token: Optional[str] = Header(default=None)) -> None:
    """수집 API 인증 의존성."""
    _check(x_service_token, settings.ingest_token, "수집")


def verify_serving(x_service_token: Optional[str] = Header(default=None)) -> None:
    """서빙(에이전트) API 인증 의존성."""
    _check(x_service_token, settings.serving_token, "서빙")
