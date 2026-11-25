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

from asgiref.sync import sync_to_async
from django.core.asgi import get_asgi_application
from django.contrib.staticfiles.handlers import ASGIStaticFilesHandler

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verbal_config.settings")


class ASGILifespanWrapper:
    """
    Handles ASGI 3.0 lifespan startup/shutdown events cleanly for Granian workers
    (which otherwise log `[WARNING] ASGI Lifespan errored, continuing without Lifespan support`
    because Django's `ASGIHandler` only accepts `http` scopes).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "lifespan":
            while True:
                message = await receive()
                msg_type = message.get("type")
                if msg_type == "lifespan.startup":
                    await send({"type": "lifespan.startup.complete"})
                elif msg_type == "lifespan.shutdown":
                    try:
                        from django.db import close_old_connections
                        await sync_to_async(close_old_connections)()
                    except Exception:
                        pass
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await self.app(scope, receive, send)


_django_asgi_app = ASGIStaticFilesHandler(get_asgi_application())
application = ASGILifespanWrapper(_django_asgi_app)
