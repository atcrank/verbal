#!/bin/bash

echo "1/2: Starting Docker containers (Redis, Ollama, Grobid)..."
docker compose up -d

echo "2/2: Starting Celery worker..."
source ../py313/bin/activate
export VERBAL_ROLE=worker

# Idempotent check: Only start watchmedo/celery if it isn't already running
if ! pgrep -f "watchmedo auto-restart" > /dev/null; then
    nohup watchmedo auto-restart --directory=./ --pattern="*.py" --recursive -- celery -A verbal_config worker -c 1 -l INFO > celery.log 2>&1 &
    echo "Background services started! (Logs are being written to celery.log)"
    echo "Run 'tail -f celery.log' to view the live logs."
else
    echo "Celery is already running."
fi