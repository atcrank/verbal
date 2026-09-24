#!/bin/bash

if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi

echo "1/3: Starting Docker containers..."
docker compose up -d

echo "2/3: Starting task worker..."
if [ -n "$PYENV_ACTIVATE" ]; then
    source "$PYENV_ACTIVATE"
else
    source ../../py312/bin/activate
fi
export VERBAL_ROLE=worker

# Idempotent check: Only start runtaskworker if it isn't already running
if ! pgrep -f "manage.py runtaskworker" > /dev/null; then
     nohup python manage.py runtaskworker > worker.log 2>&1 &
     echo "Task worker started! (Logs are being written to worker.log)"
     echo "Run 'tail -f worker.log' to view live worker logs."
else
     echo "Task worker is already running."
fi

echo "3/3: Starting task scheduler..."
if ! pgrep -f "manage.py runtaskscheduler" > /dev/null; then
     nohup python manage.py runtaskscheduler > scheduler.log 2>&1 &
     echo "Task scheduler started! (Logs are being written to scheduler.log)"
else
     echo "Task scheduler is already running."
fi