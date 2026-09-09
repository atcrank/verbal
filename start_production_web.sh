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

# Boot the lightning-fast Granian ASGI server (bound locally to 127.0.0.1 behind Nginx upstream)
HOST="${GRANIAN_HOST:-127.0.0.1}"
PORT="${GRANIAN_PORT:-${WEB_PORT:-8008}}"

GRANIAN_ARGS="--interface asgi verbal_config.asgi:application --host $HOST --port $PORT"

if [ -n "$SSL_CERT_PATH" ] && [ -n "$SSL_KEY_PATH" ]; then
    echo "SSL enabled in Granian: cert=$SSL_CERT_PATH key=$SSL_KEY_PATH ($HOST:$PORT)"
    GRANIAN_ARGS="$GRANIAN_ARGS --ssl-certificate $SSL_CERT_PATH --ssl-keyfile $SSL_KEY_PATH"
else
    echo "Serving Granian plain HTTP on $HOST:$PORT (upstream behind Nginx reverse proxy)"
fi

granian $GRANIAN_ARGS

