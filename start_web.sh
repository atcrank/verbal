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
python manage.py runserver 8000
