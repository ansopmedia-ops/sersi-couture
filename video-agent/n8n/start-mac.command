#!/bin/bash
cd "$(dirname "$0")"
if ! command -v docker >/dev/null 2>&1; then
  echo "Docker Desktop is not installed. Get it from https://www.docker.com/products/docker-desktop/"; read -n1 -p "Press any key..."; exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "Docker Desktop is not running. Open Docker Desktop, wait for 'Engine running', then double-click this file again."; read -n1 -p "Press any key..."; exit 1
fi
[ -f .env ] || cp .env.example .env
echo "Starting n8n and the video renderer. The first time takes 5-10 minutes..."
docker compose up -d --build || { echo "Something went wrong - see the messages above."; read -n1 -p "Press any key..."; exit 1; }
echo "Waiting for n8n to be ready..."
until curl -s -o /dev/null http://localhost:5678/healthz; do sleep 3; done
open http://localhost:5678
echo
echo "Ready! n8n is open in your browser at http://localhost:5678"
echo "To stop everything, double-click stop-mac.command"
