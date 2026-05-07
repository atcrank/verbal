#!/bin/bash

# Check if Celery is currently running by searching for its process
CELERY_PIDS=$(pgrep -f "watchmedo auto-restart")

if [ -n "$CELERY_PIDS" ]; then
    echo "Background services are currently RUNNING. Shutting them down..."

    echo "1/2: Stopping Celery workers gracefully..."
    pkill -f "watchmedo auto-restart"
    pkill -f "celery -A verbal_config worker"

    echo "2/2: Stopping Redis container..."
    docker compose down

    echo "Background services stopped."
else
    echo "Background services are currently STOPPED. Starting them up..."

    echo "1/2: Starting Redis container..."
    docker compose up -d

    echo "2/2: Starting Celery worker..."
    # Source the virtual environment properly
    source ../py313/bin/activate

    # Tell the Django settings to run in lightweight proxy mode
    export VERBAL_ROLE=worker

    # Run celery in the background using watchmedo for auto-restart on code changes
    nohup watchmedo auto-restart --directory=./ --pattern="*.py" --recursive -- celery -A verbal_config worker -c 1 -l INFO > celery.log 2>&1 &

    echo "Background services started! (Logs are being written to celery.log)"
    echo "Run 'tail -f celery.log' to view the live logs."
fi
