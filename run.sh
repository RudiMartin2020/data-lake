#!/usr/bin/env bash
# centos9(Linux) 안에서 실행: venv 기반 API 서버 기동
# 사용: bash run.sh
set -e
cd "$(dirname "$0")"

# .env 자동 로드(있으면) — 미들웨어 백엔드/접속정보를 API 프로세스에 주입
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

# venv 없으면 안내
if [ ! -x ".venv/bin/python" ]; then
    echo "[!] .venv 가 없습니다. 먼저 'bash setup.sh' 를 실행하세요." >&2
    exit 1
fi

export PYTHONUNBUFFERED=1
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
