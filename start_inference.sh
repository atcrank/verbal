if [ -f .env ]; then
    . .env
else
    echo "Warning: .env file not found, using .env.example"
    . .env.example
fi
export VERBAL_ROLE=inference
. ${PYENV_ACTIVATE}
python manage.py runserver 8001
