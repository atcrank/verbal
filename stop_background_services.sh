#!/bin/bash

echo "1/2: Stopping Celery workers gracefully..."
pkill -f "watchmedo auto-restart"
pkill -f "celery -A verbal_config worker"

echo "2/2: Stopping Docker containers..."
docker compose down

echo "Background services stopped."