# Windows 런처 — centos9(Linux) 컨테이너 안에서 API(run.sh) 실행.
#   사용:  .\scripts\win\run.ps1
# 내부적으로 docker exec 로 Linux bash 를 호출한다(.venv 는 Linux 전용이라 Windows 직접 실행 불가).
param(
    [string]$Container = "centos9",
    [string]$ProjectDir = "/workspace/data-lake"
)
docker exec -it $Container bash -lc "cd $ProjectDir && bash run.sh"
