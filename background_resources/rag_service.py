import os
import uuid
import json
import pickle
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain.storage import LocalFileStore, EncoderBackedStore
from langchain.retrievers.multi_vector import MultiVectorRetriever
from langchain.docstore.document import Document as LangchainDocument

from verbal_config.settings import VECTOR_STORE, CHUNK_STORE
from pydantic import BaseModel, Field
from typing import Optional, List
import outlines

class DocumentIngestion(BaseModel):
    long_description: str = Field(max_length=1000)
    condensed_summary: str = Field(max_length=300)
    keywords: List[str] = Field(max_length=3)

from pydantic import BaseModel, Field
from typing import List

class GlossaryItem(BaseModel):
    term: str = Field(..., description="The acronym or defined term")
    definition: str = Field(..., description="The full explanation or definition of the term")

class GlossaryExtraction(BaseModel):
    items: List[GlossaryItem]

class RAGService:

    db = None
    embeddings = None
    indexed_hashes = None
    store = None
    retriever = None
    chain = None
    outline_model = None
    summary_generator = None
    glossary_generator = None

    def __init__(self):
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        self.indexed_hashes = set()
        self.raw_store = LocalFileStore(CHUNK_STORE)
        self.store = EncoderBackedStore(store=self.raw_store,
                                        key_encoder=lambda x: x, # Keys are already simple ID strings
                                        value_serializer=pickle.dumps, # Function to turn Document -> Bytes
                                        value_deserializer=pickle.loads # Function to turn Bytes -> Document
        )
        self.id_key = "doc_id"

        if os.path.exists(VECTOR_STORE) and os.path.isdir(VECTOR_STORE) and "index.faiss" in os.listdir(VECTOR_STORE):
            try:
                print(f"Loading existing vector store from '{VECTOR_STORE}'...")
                self.db = FAISS.load_local(VECTOR_STORE, self.embeddings, allow_dangerous_deserialization=True)
                for doc in self.db.docstore._dict.values():
                    if 'content_hash' in doc.metadata:
                        self.indexed_hashes.add(doc.metadata['content_hash'])
                print(f"Found {len(self.indexed_hashes)} already indexed documents.")
            except Exception as e:
                print(f"Could not load vector store. Maybe something is wrong with it. Error: {e}")
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
        retrieved_context =  "Also, this is an arguably relevant excerpt from my document library:" + "\n".join(doc_cards)
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
        print(f"Retrieved {len(retrieved_docs)} documents.", retrieved_docs)
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

        self.db = Document.load_vector_store()
        self.db.save_local(VECTOR_STORE)
        print(f"Successfully synced and saved vector store to '{VECTOR_STORE}'.")
        print(f"Vector store contains {set([doc.metadata.get("content_hash") for doc in self.db.docstore._dict.values()])} documents.")
        self.summary_generator = outlines.Generator(ai_service.outline_pipeline, DocumentIngestion)
        self.glossary_generator = outlines.Generator(ai_service.outline_pipeline, GlossaryExtraction)

    def ingest_document(self, source_doc):
        print("rag_service ingest document:" , source_doc, source_doc.indexing_strategy)

        chunks, doc_ids = source_doc.convert_and_chunk_document()
        for chunk, doc_id in zip(chunks, doc_ids):
            chunk_metadata = source_doc.metadata.copy()
            chunk_metadata["doc_id"] = doc_id
            self.store.mset([(doc_id, chunk.page_content)])
            if source_doc.indexing_strategy == "RAW":
                langchain_docs = [LangchainDocument(page_content=chunk.page_content, metadata=chunk_metadata) for chunk in chunks]
                self.db.add_documents(langchain_docs)
            elif source_doc.indexing_strategy == "SUM":
                summary_text = self.get_chunk_summary(chunk.page_content)
                if summary_text:
                    summary_doc = LangchainDocument(page_content=summary_text,
                                                    metadata={**chunk_metadata, "doc_id": doc_id})
                    self.db.add_documents([summary_doc])
            elif source_doc.indexing_strategy == "DIC":
                definitions = self.get_glossary_terms(chunk.page_content)
                for item in definitions:
                    # 1. Create a NEW unique ID for this specific definition
                    # We do NOT use the chunk_id, because we want to retrieve just this definition.
                    def_id = str(uuid.uuid4())
                    # 2. Store JUST THE DEFINITION in the ByteStore
                    definition_doc = LangchainDocument(
                        page_content=item.definition,
                        metadata={
                            **chunk_metadata,
                            "type": "glossary_entry",
                            "original_term": item.term
                        }
                    )
                    self.store.mset([(def_id, definition_doc)])

                    # 3. Vectorize JUST THE TERM in FAISS
                    # Point it to the specific definition ID
                    term_doc = LangchainDocument(
                        page_content=item.term,
                        metadata={"doc_id": def_id}
                    )
                    self.db.add_documents([term_doc])
        self.indexed_hashes.add(source_doc.content_hash)

    def ingest_queryset_documents(self, queryset=None):
        """This is to be the top function for ingestion and assumes a queryset of our Django Document models.
        It might be worthwhile in other examples to split the queryset by filtering on indexing_strategy,"""

        if queryset is None:
            return

        for source_doc in queryset:
            self.ingest_document(source_doc)

        self.db.save_local(VECTOR_STORE)



    def get_chunk_summary(self, chunk):

        prompt = f"You are a data ingestion agent. Analyze the following document chunk. \n 1. If it is a Table of Contents, Index, or Copyright page, it is structural noise and no summary is needed.\n 2. Otherwise, write a short sentence describing the content of the chunk.\n Chunk: {chunk[:2000]}" # Truncate for speed if needed

        result = self.generator(prompt, repetition_penalty=1.1, max_new_tokens=1500)

        try:
            summary = DocumentIngestion.model_validate_json(result)
        except Exception as e:
            print(f"Error in summary generation: {e}")
            summary = None
        if summary:
            print("Summary obj", summary)
            if len(summary.long_description) < len(summary.condensed_summary):
                summary_text = summary.long_description
            else:
                summary_text = summary.condensed_summary
            return summary_text
        return ""


    def get_glossary_terms(self, raw_text):
        # 1. Setup the Generator
        # We ask for a LIST of items, so it handles multiple terms per page
        generator = self.glossary_generator

        prompt = f"""
        You are a precise data extraction engine. 
        Identify all acronyms, technical terms, and their definitions in the text below.
        Ignore standard filler text.

        TEXT:
        {raw_text[:1000]}
        """

        # 2. Generate
        raw_json = generator(
            prompt,
            max_new_tokens=1024,
            repetition_penalty=1.1,
        )
        print("Glossary from chunks:", raw_json)
        # 3. Validate
        try:
            result = GlossaryExtraction.model_validate_json(raw_json)
            return result.items  # Returns a list of GlossaryItem objects
        except Exception as e:
            print(f"Extraction failed: {e}")
            return []

    def ingest_glossary_doc(self, doc):
        """
        Takes raw text chunks, extracts terms, and indexes them.
        """
        raw_text_chunks = doc.convert_and_chunk_document()

        for chunk in raw_text_chunks:
            # A. Extract Terms using the LLM
            glossary_items = self.get_glossary_terms(chunk)

            for item in glossary_items:
                doc_id = str(uuid.uuid4())

                # B. Prepare the Definition (The Content)
                # We create a Document object for the definition
                definition_doc = LangchainDocument(
                    page_content=item.definition,
                    metadata={
                        "source": chunk.metadata.get("source"),
                        "type": "glossary_entry",
                        "original_term": item.term
                    }
                )

                # C. Store Definition in ByteStore (The Treasure)
                self.store.mset([(doc_id, definition_doc)])

                # D. Vectorize the TERM (The Map)
                # We embed JUST the term. This creates a very sharp vector.
                # If user searches "What is FAFO", 'FAFO' vector aligns well.
                term_doc = LangchainDocument(
                    page_content=item.term,
                    metadata={"doc_id": doc_id}  # Link to the definition
                )

                self.db.add_documents([term_doc])
                print(f"Indexed Term: {item.term}")

        # E. Save Index
        self.db.save_local(VECTOR_STORE)