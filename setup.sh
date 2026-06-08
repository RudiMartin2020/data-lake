#!/usr/bin/env bash
# centos9/운영 Linux 안에서 실행: venv 생성 + 의존성 설치
# 사용: bash setup.sh
#   - uv 가 있으면 pyproject.toml + uv.lock 기반 `uv sync`(재현 설치)
#   - uv 가 없으면 pip + requirements.txt 폴백
set -e
cd "$(dirname "$0")"

export PATH="$HOME/.local/bin:$PATH"

if command -v uv >/dev/null 2>&1; then
    echo "[*] uv 사용 ($(uv --version)) — uv sync (pyproject.toml + uv.lock)"
    uv sync                       # .python-version(3.12.12) 자동 적용, .venv 생성/동기화
else
    echo "[!] uv 미설치 → pip+venv 폴백 (시스템 python3)"
    "${PYTHON:-python3}" -m venv .venv
    .venv/bin/python -m pip install --upgrade pip -q
    .venv/bin/pip install -r requirements.txt
fi

echo
echo "[완료] Python: $(.venv/bin/python --version)"
echo "  bash run.sh            # API (http://localhost:8000/docs)"
echo "  .venv/bin/pytest -q    # 테스트"
