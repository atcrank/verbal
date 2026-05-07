docker compose up -d

(../py313/bin/activate;
celery -A verbal_config worker -l INFO &)