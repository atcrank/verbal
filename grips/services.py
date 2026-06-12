import os
import json
from django.conf import settings
from langchain_postgres import PGVector
from langchain_core.documents import Document as LangchainDocument
from llm_api.apps import service_registry
from sqlalchemy import create_engine


class GripsService:
    """
    Manages the Vector Index for the Knowledge Graph (ConceptNodes).
    Enables semantic retrieval of wiki concepts for LLM context and deduplication.
    """

    def __init__(self, collection_name="verbal_grips"):
        self.collection_name = collection_name
        self.db = None
        self.embeddings = None
        self.indexed_concepts = {}  # Map ConceptNode ID to FAISS ID

    def load_models(self):
        # Reuse the lightweight embedding model from the RAG service
        self.embeddings = service_registry.rag_service.embeddings
        db_config = settings.DATABASES['default']
        user = db_config.get("USER", "")
        password = db_config.get("PASSWORD", "")
        host = db_config.get("HOST", "127.0.0.1")
        port = db_config.get("PORT", "5432")
        db_name = db_config.get("NAME", "")
        connection_string = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}"
        self.engine = create_engine(connection_string)

        self.db = PGVector(embeddings=self.embeddings,
                           collection_name=self.collection_name,
                           connection=self.engine,
                           use_jsonb=True)

    def disconnect(self):
        """Closes SQLAlchemy connection to prevent database locks during test teardown"""
        if hasattr(self, 'engine') and self.engine:
            self.engine.dispose()


    def index_concept_node(self, node):
        """Embeds and indexes a ConceptNode for semantic retrieval."""
        if not self.db:
            self.load_models()

        # Delete the old embedding if we are updating an existing node
        old_faiss_id = self.indexed_concepts.get(node.id)
        if old_faiss_id:
            try:
                self.db.delete([old_faiss_id])
            except ValueError:
                pass

        # Create a dense representation of the concept for embedding
        search_text = f"Title: {node.title}\nContext: {node.focus_hint}\nNarrative: {node.narrative_content}"
        if node.structured_claims:
            search_text += f"\nClaims: {json.dumps(node.structured_claims)}"

        doc = LangchainDocument(
            page_content=search_text,
            metadata={"concept_id": node.id, "domain_id": node.domain_id, "title": node.title, "slug": node.slug}
        )

        self.db.add_documents([doc], ids=[str(node.id)])

    def get_grips_context(self, query: str, domain_id: int = None, k: int = 5):
        """Retrieves the most relevant ConceptNodes. Used by Chat endpoints and Benchmarks."""
        if not self.db:
            self.load_models()

        try:
            # PGVector natively supports dictionary-based metadata filtering
            filter_dict = {"domain_id": domain_id} if domain_id else None
            return self.db.similarity_search(query, k=k, filter=filter_dict)
        except Exception as e:
            print(f"Error retrieving Grips context: {e}")
            return []