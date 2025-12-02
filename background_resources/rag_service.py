import os
import uuid
import json
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.storage import LocalFileStore
from langchain.retrievers.multi_vector import MultiVectorRetriever
from langchain.docstore.document import Document as LangchainDocument

from verbal_config.settings import VECTOR_STORE
from pydantic import BaseModel, Field
from typing import Optional, List
import outlines

class DocumentIngestion(BaseModel):
    long_description: str = Field(max_length=1000)
    condensed_summary: str = Field(max_length=300)
    keywords: List[str] = Field(max_length=3)

class RAGService:

    db = None
    embeddings = None
    indexed_hashes = None
    store = None
    retriever = None
    chain = None
    generator = None

    def __init__(self):
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        self.indexed_hashes = set()
        self.store = LocalFileStore("summarised_store")
        self.id_key = "doc_id"
        self.summarising_generator = None
        if os.path.exists(VECTOR_STORE) and os.path.isdir(VECTOR_STORE) and "index.faiss" in os.listdir(VECTOR_STORE):
            try:
                print(f"Loading existing vector store from '{VECTOR_STORE}'...")
                self.db = FAISS.load_local(VECTOR_STORE, self.embeddings, allow_dangerous_deserialization=True)
                for doc in self.db.docstore._dict.values():
                    if 'content_hash' in doc.metadata:
                        self.indexed_hashes.add(doc.metadata['content_hash'])
                print(f"Found {len(self.indexed_hashes)} already indexed documents.")
            except Exception as e:
                print(f"Could not load vector store, will create a new one. Error: {e}")
        else:   # create a blank vector_store as db
            dummy_texts = ["This is a dummy document to initialize the vector store."]
            self.db = FAISS.from_texts(dummy_texts, self.embeddings)
            ids_to_delete = [self.db.index_to_docstore_id[0]]
            self.db.delete(ids_to_delete)
        self.retriever = MultiVectorRetriever(
            vectorstore=self.db,
            byte_store=self.store,
            id_key=self.id_key,
        )

        print(f"RAG Service db initialized. {self.db}")

    def save_store(self):
        self.db.save_local(VECTOR_STORE)

    def load_store(self):
        self.db = FAISS.load_local(VECTOR_STORE, self.embeddings, allow_dangerous_deserialization=True)

    def delete_document_from_vectorstore(self, content_hash):
        ids_to_delete = [
                doc_id for doc_id, doc in self.db.docstore._dict.items()
                if doc.metadata.get('content_hash') == content_hash
            ]
        self.db.delete(ids_to_delete)
        print(f"Deleted document with id {content_hash}.")

    def get_direct_context(self, query, k=1):
        retrieved_docs = self.db.similarity_search(query, k=k, search_type="mmr")  # Get top result page
        doc_cards = [f"file {i}:" +doc.metadata["filename"] + ": " + doc.page_content for i, doc in enumerate(retrieved_docs)]
        print(f"Retrieved context: {doc_cards}")
        retrieved_context =  "Also, this is an arguably relevant snippet from my document library:" + "\n".join(doc_cards)
        return retrieved_context

    def get_context(self, query: str, k: int = 4) -> list[LangchainDocument]:
        """
        Retrieves documents using the MultiVectorRetriever.

        1. Vector searches the query against the SUMMARIES in self.db.
        2. Uses the found IDs to fetch the FULL PARENT CHUNKS from self.store.
        """

        if not self.retriever:
            raise ValueError("Retriever not initialized. Ensure MultiVectorRetriever is set up.")

        # 1. Configure retrieval search parameters dynamically if needed
        # 'k' controls how many summary matches to find (and thus how many parents to return)
        self.retriever.search_kwargs = {"k": k}

        # 2. Invoke the retriever
        # The retriever handles the logic: Vector Search -> Get ID -> Lookup in ByteStore -> Return Parent
        retrieved_docs = self.retriever.invoke(query)

        # 3. Fallback/Debugging (Optional but recommended during dev)
        # If the byte_store (self.store) is empty or ids don't match, this returns empty.
        if not retrieved_docs:
            print("Warning: Retriever found no documents.")
            # Optional: fall back to raw similarity search on the vector db
            # if you want to inspect what summaries matched (for debugging)
            raw_hits = self.db.similarity_search(query, k=k)
            print(f"Raw vector hits (Summaries): {len(raw_hits)}")

        return retrieved_docs


    def load_models(self):
        """
        Syncs the vector store with the database and saves it to disk.
        Creates an empty store if one doesn't exist and no documents are found.
        """
        from background_resources.models import Document
        from llm_api.apps import service_registry
        ai_service = service_registry['ai_service']

        self.db = Document.fill_vector_store()
        self.db.save_local(VECTOR_STORE)
        print(f"Successfully synced and saved vector store to '{VECTOR_STORE}'.")
        print(f"Vector store contains {set([doc.metadata.get("content_hash") for doc in self.db.docstore._dict.values()])} documents.")
        self.generator = outlines.Generator(ai_service.outline_pipeline, DocumentIngestion)

    def add_summaries(self, queryset=None):

        if queryset is None:
            return
        file_hash_set = [hash for hash in queryset.values_list('content_hash', flat=True)]
        docset = {key: value for key, value in self.db.docstore._dict.items() if value.metadata.get("content_hash") in file_hash_set}

        for doc_id, doc_chunk in docset.items():
            if doc_chunk.metadata.get("summarised"):
                continue
            self.add_chunk_summary(doc_id)
            print("added summary for ", doc_chunk.metadata["filename"], doc_chunk.metadata["content_hash"],)

    def add_chunk_summary(self, doc_id):

        chunk_doc = self.db.docstore._dict.get(doc_id)
        self.store.mset([(doc_id, chunk_doc)])
        chunk_metadata = chunk_doc.metadata
        chunk_metadata["doc_id"] = doc_id
        chunk_metadata["summary"] = True

        if chunk_doc is None:
            return
        chunk_text = chunk_doc.page_content
        prompt = f"You are a data ingestion agent. Analyze the following document chunk. \n 1. If it is a Table of Contents, Index, or Copyright page, it is structural noise and no summary is needed.\n 2. Otherwise, write a short sentence describing the content of the chunk.\n Chunk: {chunk_text[:2000]}" # Truncate for speed if needed

        result = self.generator(prompt, repetition_penalty=1.1, max_new_tokens=1500)

        try:
            summary = DocumentIngestion.model_validate_json(result)
        except Exception as e:
            print(f"Error in summary generation: {e}")
            summary = None
        if summary:
            print("Summary obj", summary)
            summary_id = str(uuid.uuid4())
            if len(summary.long_description) < len(summary.condensed_summary):
                summary_text = summary.long_description
            else:
                summary_text = summary.condensed_summary
            self.db.add_documents([LangchainDocument(page_content=summary_text, metadata=chunk_metadata)], ids=[summary_id])
            chunk_doc.metadata["summarised"] = True


