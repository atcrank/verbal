if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi

# Load secure secrets (highest priority: ~/.verbal_secrets)
if [ -f ~/.verbal_secrets ]; then
    echo "Loading secure secrets from ~/.verbal_secrets"
    . ~/.verbal_secrets
elif [ -f .env.secrets ]; then
    echo "Loading secure secrets from .env.secrets"
    . .env.secrets
fi

# Guard: blank HF_ENDPOINT breaks huggingface_hub's URL builder
[ -z "$HF_ENDPOINT" ] && unset HF_ENDPOINT
[ -z "$PIP_INDEX_URL" ] && unset PIP_INDEX_URL

export VERBAL_ROLE=inference
. ${PYENV_ACTIVATE}

trap "echo 'Stopping inference service...'; exit 0" SIGINT SIGTERM

MIN_RESTART_INTERVAL=10
LAST_CRASH_TIME=0

while true; do
    echo "Starting inference service on port 8001..."
    python manage.py runserver 8001
    EXIT_CODE=$?
    
    CURRENT_TIME=$(date +%s)
    TIME_SINCE_LAST_CRASH=$((CURRENT_TIME - LAST_CRASH_TIME))
    LAST_CRASH_TIME=$CURRENT_TIME
    
    echo "Inference service exited with code $EXIT_CODE."
    
    if [ $EXIT_CODE -eq 0 ]; then
        echo "Clean exit detected. Stopping auto-restart."
        break
    fi
    
    if [ $TIME_SINCE_LAST_CRASH -lt $MIN_RESTART_INTERVAL ]; then
        echo "Service crashed again within ${MIN_RESTART_INTERVAL}s. Stopping to prevent infinite restart loop."
        break
    fi
    
    echo "Restarting in 5 seconds (Press Ctrl+C to stop)..."
    sleep 5
done
