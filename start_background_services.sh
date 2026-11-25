#!/bin/bash

if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi

# Load secure secrets (highest priority: ~/.verbal_secrets, then .env.secrets)
if [ -f ~/.verbal_secrets ]; then
    echo "Loading secure secrets from ~/.verbal_secrets"
    . ~/.verbal_secrets
elif [ -f .env.secrets ]; then
    echo "Loading secure secrets from .env.secrets"
    . .env.secrets
fi

echo "1/3: Starting Docker containers..."
# Start essential core infrastructure (PostgreSQL DB + Python Execution Sandbox)
for svc_pair in "db:verbal_db" "sandbox:verbal_sandbox"; do
    svc_name="${svc_pair%%:*}"
    ctr_name="${svc_pair##*:}"
    if [ "$(docker inspect -f '{{.State.Running}}' "$ctr_name" 2>/dev/null)" = "true" ]; then
        ctr_proj="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$ctr_name" 2>/dev/null)"
        if [ -n "$ctr_proj" ] && [ "$ctr_proj" != "reason" ]; then
            echo "ℹ️ Container '$ctr_name' is already running (created under compose project '$ctr_proj')."
            continue
        fi
    fi
    docker compose up -d --no-build --pull never "$svc_name"
done

# Start optional Grobid container if enabled, surfacing real errors instead of masking stderr
if [ "${ENABLE_GROBID:-true}" = "true" ]; then
    echo "Checking Grobid academic PDF container..."
    if [ "$(docker inspect -f '{{.State.Running}}' verbal_grobid 2>/dev/null)" = "true" ]; then
        grobid_proj="$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' verbal_grobid 2>/dev/null)"
        if [ -n "$grobid_proj" ] && [ "$grobid_proj" != "reason" ]; then
            echo "✅ Grobid service 'verbal_grobid' is already running on port 8070 (legacy compose project '$grobid_proj')."
        elif GROBID_ERR=$(docker compose --profile academic up -d --no-build --pull never grobid 2>&1); then
            echo "✅ Grobid service started on port 8070."
        else
            echo "⚠️ Grobid compose update warning (container is still running): $GROBID_ERR"
        fi
    elif GROBID_ERR=$(docker compose --profile academic up -d --no-build --pull never grobid 2>&1); then
        echo "✅ Grobid service started on port 8070."
    else
        echo "⚠️ Failed to start Grobid container:"
        echo "   $GROBID_ERR"
        echo "   Continuing with basic services (PDFs will fall back to PyPDFLoader chunking)."
    fi
else
    echo "ℹ️ Grobid container disabled via ENABLE_GROBID=false; continuing with basic services."
fi

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
     VERBAL_ROLE=scheduler nohup python manage.py runtaskscheduler > scheduler.log 2>&1 &
     echo "Task scheduler started! (Logs are being written to scheduler.log)"
else
     echo "Task scheduler is already running."
fi