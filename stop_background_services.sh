#!/bin/bash

echo "1/2: Stopping task workers and schedulers gracefully..."
pkill -f "manage.py runtaskworker"
pkill -f "manage.py runtaskscheduler"

echo "2/2: Stopping Docker containers..."
docker compose down

echo "Background services stopped."