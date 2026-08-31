#!/bin/bash
export DJANGO_DEBUG=False 

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

export VERBAL_ROLE=web

. ${PYENV_ACTIVATE}

# Package up static files automatically
python manage.py collectstatic --noinput

# Boot the lightning-fast Granian ASGI server
GRANIAN_ARGS="--interface asgi verbal_config.asgi:application --host 0.0.0.0"

if [ -n "$SSL_CERT_PATH" ] && [ -n "$SSL_KEY_PATH" ]; then
    PORT="${WEB_PORT:-443}"
    echo "SSL enabled: cert=$SSL_CERT_PATH key=$SSL_KEY_PATH (port $PORT)"
    GRANIAN_ARGS="$GRANIAN_ARGS --port $PORT --ssl-certificate $SSL_CERT_PATH --ssl-keyfile $SSL_KEY_PATH"
else
    PORT="${WEB_PORT:-8000}"
    echo "SSL not configured — serving plain HTTP on port $PORT"
    GRANIAN_ARGS="$GRANIAN_ARGS --port $PORT"
fi

granian $GRANIAN_ARGS

