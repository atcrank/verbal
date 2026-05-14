import os
import json
from django.conf import settings
from langchain_community.vectorstores import FAISS
from langchain.docstore.document import Document as LangchainDocument
from llm_api.apps import service_registry


class GripsService:
    """
    Manages the Vector Index for the Knowledge Graph (ConceptNodes).
    Enables semantic retrieval of wiki concepts for LLM context and deduplication.
    """

    def __init__(self, vector_store_path=None):
        self.vector_store_path = vector_store_path or os.path.join(settings.BASE_DIR, 'grips', 'vector_store')
        self.db = None
        self.embeddings = None
        self.indexed_concepts = {}  # Map ConceptNode ID to FAISS ID

    def load_models(self):
        # Reuse the lightweight embedding model from the RAG service
        self.embeddings = service_registry.rag_service.embeddings

        if os.path.exists(self.vector_store_path) and "index.faiss" in os.listdir(self.vector_store_path):
            self.db = FAISS.load_local(self.vector_store_path, self.embeddings, allow_dangerous_deserialization=True)
            # Rebuild the tracking map
            for faiss_id, doc in self.db.docstore._dict.items():
                c_id = doc.metadata.get("concept_id")
                if c_id:
                    self.indexed_concepts[c_id] = faiss_id
        else:
            self.db = FAISS.from_texts(["Initialization."], self.embeddings)
            self.db.delete([self.db.index_to_docstore_id[0]])
            os.makedirs(self.vector_store_path, exist_ok=True)
            self.db.save_local(self.vector_store_path)

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

        faiss_ids = self.db.add_documents([doc])
        self.indexed_concepts[node.id] = faiss_ids[0]
        self.db.save_local(self.vector_store_path)

    def get_grips_context(self, query: str, domain_id: int = None, k: int = 5):
        """Retrieves the most relevant ConceptNodes. Used by Chat endpoints and Benchmarks."""
        if not self.db:
            self.load_models()

        # FAISS natively supports dictionary-based metadata filtering!
        filter_dict = {"domain_id": domain_id} if domain_id else None
        return self.db.similarity_search(query, k=k, filter=filter_dict)