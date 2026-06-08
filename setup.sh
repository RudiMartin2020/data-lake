#!/usr/bin/env bash
# centos9/운영 Linux 안에서 실행: uv 기반 venv 생성 + 의존성 설치
# 사용: bash setup.sh
#   - PYTHON_VERSION 환경변수로 버전 지정(기본 3.12.12)
#   - uv 가 없으면 pip+venv 폴백
set -e
cd "$(dirname "$0")"

PYV="${PYTHON_VERSION:-3.12.12}"
export PATH="$HOME/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
    echo "[*] uv 사용 ($(uv --version)) — Python ${PYV}"
    uv python install "$PYV" 2>/dev/null || true   # 에어갭이면 시스템/반입본 사용
    uv venv --python "$PYV" .venv
    uv pip install -r requirements.txt
else
    echo "[!] uv 미설치 → pip+venv 폴백 (시스템 python3 사용)"
    "${PYTHON:-python3}" -m venv .venv
    .venv/bin/python -m pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt
fi

echo
echo "[완료] Python: $(.venv/bin/python --version)"
echo "  bash run.sh            # API (http://localhost:8000/docs)"
echo "  .venv/bin/pytest -q    # 테스트"
