#!/bin/bash

# Check if background task worker is currently running
WORKER_PIDS=$(pgrep -f "manage.py runtaskworker")

if [ -n "$WORKER_PIDS" ]; then
    echo "Background services are currently RUNNING. Shutting them down..."

    echo "1/2: Stopping task worker and scheduler..."
    pkill -f "manage.py runtaskworker"
    pkill -f "manage.py runtaskscheduler"

    echo "2/2: Stopping Docker containers..."
    docker compose down

    echo "Background services stopped."
else
    echo "Background services are currently STOPPED. Starting them up..."

    echo "1/3: Starting Docker containers..."
    docker compose up -d

    echo "2/3: Sourcing environment..."
    if [ -n "$PYENV_ACTIVATE" ]; then
        source "$PYENV_ACTIVATE"
    else
        source ../../py312/bin/activate
    fi
    export VERBAL_ROLE=worker

    echo "3/3: Starting task worker and scheduler..."
    nohup python manage.py runtaskworker > worker.log 2>&1 &
    nohup python manage.py runtaskscheduler > scheduler.log 2>&1 &

    echo "Background task worker and scheduler started!"
    echo "Run 'tail -f worker.log' to view live worker logs."
fi
