"""FastAPI 진입점.

부팅 시 버킷/카탈로그/데이터 루트를 부트스트랩하고 라우터를 등록한다.
Swagger UI / ReDoc 은 CDN 대신 로컬 정적 파일(app/static)로 서빙한다(폐쇄망 대응).
실행:  uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.docs import (
    get_redoc_html,
    get_swagger_ui_html,
    get_swagger_ui_oauth2_redirect_html,
)
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .routers import agent, health, ingest

STATIC_DIR = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 데이터 루트/버킷/카탈로그 부트스트랩 (storage·catalog import 시 자동 생성)
    settings.data_root.mkdir(parents=True, exist_ok=True)
    from . import storage as _storage  # noqa: F401  (스토리지 백엔드 초기화)
    from . import catalog as _catalog  # noqa: F401  (스키마 생성)
    yield


# 기본 docs(CDN) 비활성화 → 아래에서 로컬 정적 파일 기반으로 재정의
app = FastAPI(
    title=settings.app_name,
    version=__version__,
    description="RPA Data-to-AI 통합 시스템 — 비동기 수집 + 에이전트 Tool API",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
)

# 정적 파일(Swagger/ReDoc 에셋) — 폐쇄망에서도 동작
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.include_router(health.router)
app.include_router(ingest.router)
app.include_router(agent.router)


@app.get("/docs", include_in_schema=False)
def swagger_ui_html():
    return get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - Swagger UI",
        oauth2_redirect_url=app.swagger_ui_oauth2_redirect_url,
        swagger_js_url="/static/swagger-ui-bundle.js",
        swagger_css_url="/static/swagger-ui.css",
        swagger_favicon_url="/static/favicon.png",
        # validator 배지의 외부 호출(validator.swagger.io) 차단 — 폐쇄망 대응
        swagger_ui_parameters={"validatorUrl": None},
    )


@app.get(app.swagger_ui_oauth2_redirect_url, include_in_schema=False)
def swagger_ui_redirect():
    return get_swagger_ui_oauth2_redirect_html()


@app.get("/redoc", include_in_schema=False)
def redoc_html():
    return get_redoc_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} - ReDoc",
        redoc_js_url="/static/redoc.standalone.js",
        redoc_favicon_url="/static/favicon.png",
        with_google_fonts=False,  # 폐쇄망: 외부 폰트 미사용
    )


@app.get("/", tags=["system"])
def root() -> dict:
    return {
        "service": settings.app_name,
        "version": __version__,
        "docs": "/docs",
        "redoc": "/redoc",
        "endpoints": [
            "POST /ingest",
            "GET /ingest/status/{task_id}",
            "GET /agent/tools/schema",
            "POST /agent/tools/query",
            "GET /health",
            "GET /info",
        ],
    }
