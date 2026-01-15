import os
import uuid
import json
import pickle
import re
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
        print(f"RAG Service db initialized. {self.db}")
        if not os.path.exists("index_dump.txt"):
            self.dump_index_to_file()


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

    def get_context(self, query: str, k: int = 4) -> List[LangchainDocument]:
        """
        Manually retrieves documents:
        1. Vector searches the query against the SUMMARIES/TERMS in self.db.
        2. Uses the found 'doc_id's to fetch the FULL PARENT CHUNKS from self.store.
        """

        # 1. Vector Search (Get the summaries/terms)
        # We fetch k*2 to handle cases where multiple summaries point to the same parent
        sub_docs = self.db.similarity_search(query, k=k * 2)

        if not sub_docs:
            return []

        # 2. Extract Unique Parent IDs
        parent_ids = []
        seen_ids = set()

        for doc in sub_docs:
            # The 'doc_id' we saved in ingest_document
            p_id = doc.metadata.get("doc_id")

            if p_id and p_id not in seen_ids:
                parent_ids.append(p_id)
                seen_ids.add(p_id)

            if len(parent_ids) >= k:
                break

        # 3. Retrieve Full Documents from the Store
        # This is where we use your store directly, bypassing the Retriever's bugs
        try:
            # mget returns the objects directly (Documents), thanks to pickle.loads
            results = self.store.mget(parent_ids)

            # Filter out any Nones (in case a key was missing)
            final_docs = [doc for doc in results if doc is not None]

            print(f"Retrieved {len(final_docs)} full documents from {len(sub_docs)} vector hits.")
            return final_docs

        except Exception as e:
            print(f"Error during manual store retrieval: {e}")
            return []

    def load_models(self):
        """
        Syncs the vector store with the database and saves it to disk.
        Creates an empty store if one doesn't exist and no documents are found.
        """
        from background_resources.models import Document
        from llm_api.apps import service_registry
        ai_service = service_registry['ai_service']

        # self.db = Document.load_vector_store()
        # self.db.save_local(VECTOR_STORE)
        print(f"Successfully synced and saved vector store to '{VECTOR_STORE}'.")
        print(f"Vector store contains {set([doc.metadata.get("content_hash") for doc in self.db.docstore._dict.values()])} documents.")
        print(f"Vector store contains {set([doc.metadata.get("filename") for doc in self.db.docstore._dict.values()])} documents.")
        self.summary_generator = outlines.Generator(ai_service.outline_pipeline, DocumentIngestion)
        self.glossary_generator = outlines.Generator(ai_service.outline_pipeline, GlossaryExtraction)

    def ingest_document(self, source_doc):
        if (source_doc.content_hash in self.indexed_hashes) and source_doc.valid_current_index():
            print("source_doc already indexed ", source_doc.metadata.get(
                "chunking_scheme", ""), " sceheme.")
            return
        if (source_doc.content_hash in self.indexed_hashes) and not source_doc.valid_current_index():
            self.delete_document_from_vectorstore(source_doc.content_hash)
            print("source_doc must be deleted and re-indexed ", source_doc.metadata.get(
                "chunking_scheme", ""), " sceheme.")
        print("rag_service ingesting document:" , source_doc, source_doc.indexing_strategy)

        chunks, doc_ids = source_doc.convert_and_chunk_document()
        faiss_docs_to_add = []
        for chunk, doc_id in zip(chunks, doc_ids):
            chunk_metadata = source_doc.metadata.copy()
            chunk_metadata["doc_id"] = doc_id
            chunk_metadata["content_hash"] = source_doc.content_hash
            chunk_metadata["filename"] = source_doc.file.name
            chunk_metadata["page_number"] = 0
            chunk_metadata["chunking_scheme"] = source_doc.chunking_scheme()
            self.store.mset([(doc_id, chunk)])
            if source_doc.indexing_strategy == "RAW":
                raw_doc = LangchainDocument(page_content=chunk.page_content, metadata={**chunk_metadata, "doc_id": doc_id})
                faiss_docs_to_add.append(raw_doc)
            elif source_doc.indexing_strategy == "SUM":
                summary_text = self.get_chunk_summary(chunk.page_content)
                if summary_text:
                    summary_doc = LangchainDocument(page_content=summary_text,
                                                    metadata={**chunk_metadata, "doc_id": doc_id})
                    faiss_docs_to_add.append(summary_doc)
            elif source_doc.indexing_strategy == "DIC":
                definitions = self.get_glossary_terms(chunk.page_content, source_doc=source_doc)
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
                    faiss_docs_to_add.append(term_doc)
        self.db.add_documents(faiss_docs_to_add)
        self.indexed_hashes.add(source_doc.content_hash)
        source_doc.currently_indexed = True
        source_doc.metadata["chunking_scheme"] = source_doc.chunking_scheme()

        source_doc.save()
        self.save_store()

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

        # Outlines v1 returns the Pydantic object directly
        summary = self.summary_generator(prompt, repetition_penalty=1.1, max_new_tokens=1500)

        if summary:
            print("Summary obj", summary)
            if len(summary.long_description) < len(summary.condensed_summary):
                summary_text = summary.long_description
            else:
                summary_text = summary.condensed_summary
            return summary_text
        return ""

    def is_hallucination(self, term: str, chunk_text: str) -> bool:
        """
        Returns True if the term is NOT found in the source text.
        Uses case-insensitive matching to be forgiving of capitalization.
        """
        # 1. Clean both inputs
        clean_term = term.strip().lower()
        clean_text = chunk_text.lower()

        # 2. Strict Check: Term must appear in the text
        if clean_term not in clean_text:
            return True

        return False

    def check_definition_grounding(self, definition: str, chunk_text: str) -> bool:
        def_words = set(definition.lower().split())
        chunk_words = set(chunk_text.lower().split())

        # Calculate intersection (common words)
        common = def_words.intersection(chunk_words)

        # Remove stopwords to be accurate (the, a, is, etc.)
        stopwords = {'the', 'a', 'an', 'is', 'are', 'of', 'to', 'in'}
        meaningful_overlap = common - stopwords

        # If the definition has ZERO meaningful words from the text, it's likely an external hallucination
        return len(meaningful_overlap) > 0

    def get_glossary_terms(self, raw_text, source_doc=None):
        # 0. Check for regex override
        if source_doc and source_doc.extraction_regex:
            try:
                # Security Warning: User-supplied regexes can cause ReDoS (Regular Expression Denial of Service).
                # Ensure that only trusted administrators can modify the extraction_regex field.
                regex = re.compile(source_doc.extraction_regex)
                matches = regex.findall(raw_text)
                if matches:
                    print(f"Using regex extraction for {source_doc.title}")
                    valid_definitions = []
                    for match in matches:
                        # Expecting (term, definition) tuple from regex groups
                        if len(match) >= 2:
                            term = match[0].strip()
                            definition = match[1].strip()
                            valid_definitions.append(GlossaryItem(term=term, definition=definition))
                    return valid_definitions
            except re.error as e:
                print(f"Invalid regex for {source_doc.title}: {e}")
                # Fallback to LLM if regex fails? Or just log error?
                pass

        # 1. Setup the Generator
        # We ask for a LIST of items, so it handles multiple terms per page
        generator = self.glossary_generator

        prompt = f"""
        You are a strict data extraction engine. 
        Your job is to extract definitions explicitly stated in the text below.
        
        RULES:
        1. ONLY extract terms that are defined in the provided text.
        2. If a term is mentioned but not defined, IGNORE it.
        3. Do NOT use your own knowledge. If the text says "Apple is a fruit", extract it. If it just says "Apple", do not invent a definition.
        4. Return an empty list if no definitions are found.
        
        Usually the separator is a colon (:), but a dash (-) is also used. 
        Ignore standard filler text, such as headings, footers, page numbers. Do precise data extraction by transcribing - limit yourself to the text itself. 
        You may correct typographical errors in the source.

        TEXT TO ORGANIZE INTO DEFINITIONS:
        {raw_text[:1000]}
        """

        # 2. Generate
        # Outlines v1 returns the Pydantic object directly
        result = generator(
            prompt,
            max_new_tokens=1024,
            repetition_penalty=1.1,
        )
        print("Glossary from chunks:", result)

        if result:
            valid_definitions = []
            for item in result.items:
                if self.is_hallucination(item.term, raw_text):
                    print("Hallucination detected:", item.term, " not in", raw_text)
                if not self.check_definition_grounding(item.definition, raw_text):
                    print("Definition not grounding:", item.definition, " not found in", raw_text)
                if not self.is_hallucination(item.term, raw_text) and self.check_definition_grounding(item.definition, raw_text):
                    valid_definitions.append(item)

            return valid_definitions
        return []

    def parse_glossary_deterministic(self, source_doc):
        """
        Parses a glossary text block into a dictionary.
        Handles 'Term: Definition' and 'Term - Definition'.
        Ignores single-letter headers (A, B, C...).
        """
        glossary_dict = {}
        text_content = ""
        with source_doc.file.open(mode="r") as f:
            text_content = f.read()

        # Split into lines
        lines = text_content.split('\n')

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # 1. Skip Alphabetical Headers (e.g., "A", "B")
            if len(line) == 1 and line.isalpha():
                continue

            # 2. Check for Splitters (Colon or Dash)
            # We look for the FIRST occurrence of ": " or " - "
            # We use regex to be safe about spacing
            match = re.search(r'(.+?)(?:\s*:\s*|\s+-\s+)(.+)', line)

            if match:
                term = match.group(1).strip()
                definition = match.group(2).strip()

                # Simple heuristic: Terms shouldn't be massive sentences.
                # If the "Term" is > 10 words, it's likely just a sentence with a dash in it.
                if len(term.split()) > 10:
                    continue

                glossary_dict[term] = definition

        return glossary_dict

    def dump_index_to_file(self, output_filename="index_dump.txt"):
        """
        Writes the entire contents of the vector store + byte store to a text file.
        Format:
        [TERM/SUMMARY] -> [FULL CONTENT] (Source)
        """
        from background_resources.models import Document
        indexed_false_positives = []  # hallucinated entries
        indexed_misses = []           #  entries not found in index

        d = Document.objects.get(content_hash="3c8a5cd8d24565ed37301cedec18da26f51bc447e094022755d934321794c8a2")
        deterministic_glossary_dict = self.parse_glossary_deterministic(d)
        with open("glossary_read_dump.txt", "w", encoding="utf-8") as f:
            f.write(f"--- DUMP OF {len(deterministic_glossary_dict.keys())} Deterministic Glossary Dict ITEMS ---\n\n")

            for i, (key, value) in enumerate(deterministic_glossary_dict.items()):
                f.write(f"Entry #{i + 1}--------------------------------------------\n")
                f.write(f"Index Key (Term): {key}")
                f.write(f"\nDefinition: {value}\n")
            print("dumped dict")

        print(f"Dumping index to {output_filename}...")
        index_docs = [doc for doc in self.db.docstore._dict.values()]
        index_dict = {}
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write(f"--- DUMP OF {len(index_docs)} INDEXED ITEMS ---\n\n")

            for i, index_doc in enumerate(index_docs):
                # The text used for searching (e.g. The Glossary Term)
                search_term = index_doc.page_content.replace("\n", " ")

                # The ID pointing to the full content
                doc_id = index_doc.metadata.get("doc_id")

                # Retrieve the full content (e.g. The Definition)
                # We use the store directly
                full_content_doc = self.store.mget([doc_id])[0]

                if full_content_doc:
                    body_text = full_content_doc.page_content.strip()
                    source = full_content_doc.metadata.get("filename", "Unknown Source")
                    page = full_content_doc.metadata.get("page_number", "?")
                    index_dict.update({search_term: body_text})
                    if deterministic_glossary_dict.get(search_term) is None:
                        indexed_false_positives.append(search_term)

                else:
                    body_text = "[MISSING IN STORE - ORPHANED INDEX]"
                    source = "N/A"
                    page = "N/A"

                # Write to file
                f.write(f"Entry #{i + 1}\n")
                f.write(f"Index Key (Term): {search_term}\n")
                f.write(f"Content:          {body_text}\n")
                f.write(f"Source:           {source} (Page {page})\n")
                f.write("-" * 60 + "\n")

        print("Dumped vector_store.")
        for key, value in deterministic_glossary_dict.items():
            if index_dict.get(key) is None:
                indexed_misses.append(key)

        print(f"Found {len(indexed_misses)} misses / {len(deterministic_glossary_dict.keys())} and ")
        print(f"{len(indexed_false_positives)} hallucinations / {len(index_dict)} total.")
        print("Index Misses:", "\n-".join(indexed_misses))
        print("Index Hallucinations:", "\n-".join(indexed_false_positives))