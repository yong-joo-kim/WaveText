@echo off
rem WaveText 개발용 실행 (더블클릭). 배포판은 WaveText.exe 를 사용한다.
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"
if not exist ".venv\Scripts\pythonw.exe" (
  echo [오류] 가상환경이 없습니다. 아래 명령으로 먼저 만드세요.
  echo   py -3.14 -m venv .venv
  echo   .venv\Scripts\python -m pip install -r requirements-dev.txt
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m wavetext
