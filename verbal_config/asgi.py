"""
ASGI config for verbal_config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verbal_config.settings")

application = get_asgi_application()
application = ASGIStaticFilesHandler(application)

# # Initialize AI services on startup
# from llm_api.apps import service_registry
# service_registry['ai_service'].load_models()
# service_registry['rag_service'].load_models()
