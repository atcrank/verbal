from django.apps import AppConfig

import sys
import threading

class LazyServiceRegistry:
    """
    A registry that instantiates services only when they are first accessed.
    Thread-safe to allow background loading.
    """
    def __init__(self):
        # We use RLock (Re-entrant Lock) because initializing rag_service 
        # may access ai_service, requiring the lock to be acquired twice by the same thread.
        self._lock = threading.RLock()
        self._ai_service = None
        self._nlp_service = None
        self._rag_service = None

    def _should_skip_loading(self):
        if len(sys.argv) > 1:
            command = sys.argv[1]
            if command in ['makemigrations', 'migrate', 'collectstatic', 'showmigrations', 'check', 'help']:
                return True
        return False

    @property
    def ai_service(self):
        if self._should_skip_loading():
            return None
        
        if self._ai_service is None:
            with self._lock:
                if self._ai_service is None:
                    print("Initializing AI Service...")
                    from .ai_service import AIService
                    ai_serv = AIService()
                    ai_serv.load_models()
                    self._ai_service = ai_serv
        return self._ai_service

    @property
    def nlp_service(self):
        if self._should_skip_loading():
            return None
        
        if self._nlp_service is None:
            with self._lock:
                if self._nlp_service is None:
                    print("Initializing NLP Service...")
                    from background_resources.nlp_service import NLPService
                    self._nlp_service = NLPService()
        return self._nlp_service

    @property
    def rag_service(self):
        if self._should_skip_loading():
            return None
        
        if self._rag_service is None:
            with self._lock:
                if self._rag_service is None:
                    print("Initializing RAG Service...")
                    from background_resources.rag_service import RAGService
                    rag_serv = RAGService()
                    rag_serv.load_models()
                    self._rag_service = rag_serv
        return self._rag_service

    def reload_ai_service(self):
        """
        Unloads the current AI service (freeing VRAM) and re-initializes it.
        Used when switching models at runtime.
        """
        with self._lock:
            if self._ai_service:
                self._ai_service.unload_models()
                self._ai_service = None # Force re-creation on next access
            
            # Trigger the property to reload immediately
            _ = self.ai_service
            # Note: RAG service might hold stale references to generators? 
            # RAGService.load_models() grabs ai_service fresh, but the outline wrappers?
            # We should probably reload RAG models too if they depend on specific tokenizers
            if self._rag_service:
                 self._rag_service.load_models()

    # OPTIONAL: Compatibility with existing code
    # If your code uses service_registry["name"], keep this method.
    def __getitem__(self, item):
        if hasattr(self, item):
            return getattr(self, item)
        raise KeyError(f"Service '{item}' not registered.")


# Replace the old dict with this instance
service_registry = LazyServiceRegistry()


class LlmApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'llm_api'
