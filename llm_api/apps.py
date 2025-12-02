from django.apps import AppConfig
import threading
import sys

from background_resources.rag_service import RAGService

# Use a lock to ensure models are loaded only once
model_lock = threading.Lock()
# This registry will hold our live service
service_registry = {}

class LlmApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'llm_api'

    def ready(self):
        # This code runs once when the app is ready

        # We check if 'ai_service' is already loaded.

        # This prevents running this in child processes (like the reloader)
        if 'ai_service' in service_registry:
            return
        if 'rag_service' in service_registry:
            return
        # Check if we want the server or just need to run e.g. a management command
        # Todo: there are other server host commands
        if not ('runserver' in sys.argv or 'gunicorn' in sys.argv or 'uvicorn' in sys.argv):
            return
        with model_lock:
            # Check again inside the lock (double-checked locking)
            if 'ai_service' not in service_registry:
                print("--- Django App is Ready: Initializing AI Service ---")
                from .ai_service import AIService

                # Now we create the instance and load models
                ai_service = AIService()
                ai_service.load_models()

                # Store the live, ready-to-use service in our registry
                service_registry['ai_service'] = ai_service
                print("--- AI Service Loaded and Ready ---")
            if 'rag_service' not in service_registry:
                from background_resources.rag_service import RAGService
                rag_service = RAGService()
                service_registry['rag_service'] = rag_service
                rag_service.load_models()
                print("--- RAG Service Loaded and Ready ---")


    # default_auto_field = "django.db.models.BigAutoField"
    # name = "llm_api"
    #
    # def ready(self):
    #     # This method is called once Django is initialized.
    #     # This is the perfect place for our one-time setup.
    #     from .ai_service import ai_service
    #
    #     # Only load models when running the server, not during migrations, etc.
    #     import sys
    #     if 'runserver' in sys.argv or 'gunicorn' in sys.argv or 'uvicorn' in sys.argv:
    #         ai_service.load_models()
    #         rag_service.load_models()
    #
