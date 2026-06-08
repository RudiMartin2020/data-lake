# Windows 런처 — venv 생성 + 의존성 설치(setup.sh, uv/Python 3.12.12).
#   사용:  .\scripts\win\setup.ps1
param(
    [string]$Container = "centos9",
    [string]$ProjectDir = "/workspace/data-lake"
)
docker exec -it $Container bash -lc "cd $ProjectDir && bash setup.sh"
