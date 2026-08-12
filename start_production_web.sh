#!/bin/bash
export DJANGO_DEBUG=False 

if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi
export VERBAL_ROLE=web

. ${PYENV_ACTIVATE}

# Package up static files automatically
python manage.py collectstatic --noinput

# Boot the lightning-fast Granian ASGI server
granian --interface asgi verbal_config.asgi:application --host 0.0.0.0 --port 8000
