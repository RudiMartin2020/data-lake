#!/usr/bin/env bash
# centos9(Linux) 안에서 실행: 현재 폴더에 venv 생성 + 의존성 설치
# 사용: bash setup.sh
set -e
cd "$(dirname "$0")"

PY="${PYTHON:-python3}"

if [ ! -x ".venv/bin/python" ]; then
    echo "[*] .venv 생성 (${PY})"
    "$PY" -m venv .venv
fi

echo "[*] pip 업그레이드"
.venv/bin/python -m pip install --upgrade pip -q

echo "[*] 의존성 설치 (requirements.txt)"
.venv/bin/pip install -r requirements.txt

echo
echo "[완료] 다음으로 실행하세요:"
echo "  source .venv/bin/activate   # (선택) 셸 활성화"
echo "  bash run.sh                 # API 서버 (http://localhost:8000/docs)"
echo "  .venv/bin/pytest -q         # 테스트"
