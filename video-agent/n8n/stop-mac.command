#!/bin/bash
cd "$(dirname "$0")"
docker compose stop
echo "Stopped. Double-click start-mac.command to start again."
