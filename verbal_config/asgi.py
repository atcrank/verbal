"""
ASGI config for verbal_config project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os
import warnings

warnings.filterwarnings("ignore", message=r".*allowed_objects.*")
warnings.filterwarnings("ignore", category=DeprecationWarning, module=r"langgraph.*")
warnings.filterwarnings("ignore", category=PendingDeprecationWarning, module=r"langgraph.*")

from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verbal_config.settings")

application = get_asgi_application()
application = ASGIStaticFilesHandler(application)
