import threading
from llm_api.apps import service_registry


class BackgroundModelLoaderMiddleware:
    """
    Middleware to trigger AI model loading in a background thread on the first request.
    This utilizes 'dead time' while the user is navigating the site.
    """
    _loading_triggered = False
    _lock = threading.Lock()

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Check if loading has been triggered yet
        if not BackgroundModelLoaderMiddleware._loading_triggered:
            with BackgroundModelLoaderMiddleware._lock:
                if not BackgroundModelLoaderMiddleware._loading_triggered:
                    BackgroundModelLoaderMiddleware._loading_triggered = True
                    # Start loading in a separate thread to avoid blocking the response
                    print("BackgroundModelLoaderMiddleware: Starting background model load...")
                    thread = threading.Thread(target=self._load_services, daemon=True)
                    thread.start()

        return self.get_response(request)

    def _load_services(self):
        try:
            # Accessing the properties triggers the thread-safe initialization.
            # Accessing rag_service will also trigger ai_service because of dependencies.
            _ = service_registry.rag_service
            _ = service_registry.nlp_service
            print("BackgroundModelLoaderMiddleware: Models loaded successfully in background.")
        except Exception as e:
            print(f"BackgroundModelLoaderMiddleware: Error loading models: {e}")