@echo off
cd /d "%~dp0"
docker compose stop
echo Stopped. Double-click start-windows.bat to start again.
pause
