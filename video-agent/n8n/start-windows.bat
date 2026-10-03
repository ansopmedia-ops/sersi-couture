@echo off
title Script to Video - start
cd /d "%~dp0"
where docker >nul 2>nul || (echo Docker Desktop is not installed. Get it from https://www.docker.com/products/docker-desktop/ & pause & exit /b 1)
docker info >nul 2>nul || (echo Docker Desktop is not running. Open Docker Desktop, wait until it says "Engine running", then double-click this file again. & pause & exit /b 1)
if not exist .env copy .env.example .env >nul
echo Starting n8n and the video renderer. The first time takes 5-10 minutes...
docker compose up -d --build || (echo Something went wrong - see the messages above. & pause & exit /b 1)
echo Waiting for n8n to be ready...
:wait
timeout /t 3 /nobreak >nul
curl -s -o nul http://localhost:5678/healthz || goto wait
start "" http://localhost:5678
echo.
echo Ready! n8n is open in your browser at http://localhost:5678
echo You can close this window. To stop everything, double-click stop-windows.bat
pause
