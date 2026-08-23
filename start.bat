@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo.
echo  투자 타이밍 모니터를 시작합니다.
echo  이 창을 닫으면 서버가 종료됩니다.
echo.
start "" http://localhost:8000/timing_monitor_v2.html
python -m http.server
pause
