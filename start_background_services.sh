#!/bin/bash

if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi

echo "1/3: Starting Docker containers..."
docker compose up -d

echo "2/3: Starting Celery worker..."
if [ -n "$PYENV_ACTIVATE" ]; then
    source "$PYENV_ACTIVATE"
else
    source ../../py313/bin/activate
fi
export VERBAL_ROLE=worker

# Idempotent check: Only start watchmedo/celery if it isn't already running
if ! pgrep -f "watchmedo auto-restart" > /dev/null; then
     nohup watchmedo auto-restart --directory=./ --pattern="*.py" --recursive -- celery -A verbal_config worker -c 1 -l INFO > celery.log 2>&1 &
     echo "Background services started! (Logs are being written to celery.log)"
     echo "Run 'tail -f celery.log' to view the live logs."
 else
     echo "Celery is already running."
 fi

 echo "3/3: Starting Celery Beat Scheduler..."
 if ! pgrep -f "celery -A verbal_config beat" > /dev/null; then
     nohup celery -A verbal_config beat -l INFO --scheduler django_celery_beat.schedulers:DatabaseScheduler > celery_beat.log 2>&1 &
     echo "Background services and scheduler fully started!"
 else
     echo "Celery Beat is already running."
 fi