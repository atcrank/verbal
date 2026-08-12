import logging
logger = logging.getLogger(__name__)

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
        self._grips_service = None

    def _should_skip_loading(self):
        if len(sys.argv) > 1:
            command = sys.argv[1]
            if command in ['makemigrations', 'migrate', 'collectstatic', 'showmigrations', 'check', 'help']:
                return True
        return False

    def _sync_hosting_backend_state(self):
        """Reads the system config and ensures the Docker containers and VRAM state match."""
        import sys
        if 'test' in sys.argv:
            return

        try:
            from .models import SystemConfiguration
            from . import ollama_client
            from . import vllm_client
            config = SystemConfiguration.get_solo()
            if config:
                logger.info(f"SYNC: Syncing hosting backend state. Active backend is: {config.hosting_backend}")
                if config.hosting_backend == 'vllm':
                    logger.info("SYNC: Stopping Ollama and ensuring vLLM is running...")
                    ollama_client.stop_container()
                    if config.active_vllm_model:
                        vllm_client.start_container(config.active_vllm_model.hf_model_id)
                elif config.hosting_backend == 'ollama':
                    logger.info("SYNC: Stopping vLLM and ensuring Ollama is running...")
                    vllm_client.stop_container()
                    ollama_client.start_container()
                    if config.active_ollama_model:
                        logger.info(f"SYNC: Loading Ollama model: {config.active_ollama_model.hf_model_id}")
                        ollama_client.set_ollama_model_state(config.active_ollama_model.hf_model_id, active=True)
                elif config.hosting_backend == 'pytorch':
                    logger.info("SYNC: Stopping both Ollama and vLLM containers...")
                    vllm_client.stop_container()
                    ollama_client.stop_container()
        except Exception as e:
            logger.info(f'SYNC: Skipping backend startup sync due to error: {e}')

    @property
    def ai_service(self):
        if self._should_skip_loading():
            return None
        
        if self._ai_service is None:
            with self._lock:
                if self._ai_service is None:
                    self._sync_hosting_backend_state()
                    logger.info('Initializing AI Service...')
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
                    logger.info('Initializing NLP Service...')
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
                    logger.info('Initializing RAG Service...')
                    from background_resources.rag_service import RAGService
                    rag_serv = RAGService()
                    rag_serv.load_models()
                    self._rag_service = rag_serv
        return self._rag_service

    @property
    def grips_service(self):
        if self._should_skip_loading():
            return None
        
        if self._grips_service is None:
            with self._lock:
                if self._grips_service is None:
                    logger.info('Initializing Grips Service...')
                    from grips.services import GripsService
                    grips_serv = GripsService()
                    grips_serv.load_models()
                    self._grips_service = grips_serv
        return self._grips_service

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

    def ready(self):
        from django.conf import settings
        import os
        import threading
        
        # Prevent running twice in dev mode with the auto-reloader
        if os.environ.get('RUN_MAIN', None) != 'true' and settings.DEBUG:
            return
            
        role = getattr(settings, 'VERBAL_ROLE', 'standalone')
        if role in ['inference', 'standalone']:
            logger.info(f"🚀 Eagerly loading AI models in background thread (Role: {role})...")
            
            def load_models():
                try:
                    # Accessing the property triggers the lazy initialization
                    _ = service_registry.ai_service
                    logger.info("✅ Background AI model loading complete.")
                except Exception as e:
                    logger.error(f"❌ Failed to eagerly load AI models: {e}")
            
            t = threading.Thread(target=load_models, daemon=True)
            t.start()
