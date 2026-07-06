import logging
logger = logging.getLogger(__name__)

import os
import torch
if not hasattr(torch, "float8_e8m0fnu"):
    setattr(torch, "float8_e8m0fnu", None)

from uuid import uuid4
import json
import zipfile
import re
from django.conf import settings
from django.db import models
from django.db.models import Count
from django.utils import timezone

# You will need to have these libraries installed:
# pip install langchain langchain-community langchain-postgres sentence-transformers
from langchain_text_splitters import CharacterTextSplitter, RecursiveCharacterTextSplitter
from langchain_core.documents import Document as LangchainDocument
from langchain_community.document_loaders import (PyPDFLoader, Docx2txtLoader, UnstructuredPowerPointLoader, RecursiveUrlLoader, DirectoryLoader, BSHTMLLoader, NotebookLoader)
from bs4 import BeautifulSoup as Soup
from langchain_postgres import PGVector
from langchain_huggingface import HuggingFaceEmbeddings
from pydantic import BaseModel, Field
import outlines
from sqlalchemy import create_engine
from typing import TYPE_CHECKING, Optional, List, Tuple, Literal

from background_resources.models import (Document as DjangoDocument, 
                                         RAGChunk as DjangoChunk,
                                        StrategyChunkUsage,
                                         ReadingStrategy as DjangoReadingStrategy,
                                         GrobidReadingStrategy,
                                         RAGQueryLog, 
                                         PromptStrategy, 
                                         RegexStrategy, 
                                         AbbreviationsReadingStrategy) 



class DocumentHandles(BaseModel):
    long_form: str = Field(max_length=1000)
    short_form: str = Field(max_length=300)
    keywords: List[str] = Field(max_length=3)

class GlossaryItem(BaseModel):
    term: str = Field(..., description="The acronym or defined term")
    definition: str = Field(..., description="The full explanation or definition of the term")

class GlossaryExtraction(BaseModel):
    items: List[GlossaryItem]


OUTPUT_TYPES = {"DocumentHandles": DocumentHandles,
                "GlossaryItem": GlossaryItem,
                "GlossaryExtraction": GlossaryExtraction}


class DjangoChunkStore:
    """Seamlessly replaces LocalFileStore by reading/writing directly to the RAGChunk Django model."""
    def mget(self, keys):
        # Coerce all keys to strings to prevent UUID object hash mismatch in dict lookups!
        str_keys = [str(k) for k in keys]
        logger.info(str_keys)
        chunks = DjangoChunk.objects.filter(chunk_id__in=str_keys)
        chunk_dict = {str(c.chunk_id): LangchainDocument(page_content=c.text_content or "", metadata=c.metadata) for c in chunks}
        logger.info(chunk_dict)
        chunk_list = [chunk_dict.get(k) for k in str_keys]
        logger.info(chunk_list)
        return chunk_list

    def mset(self, kv_pairs):
        for k, v in kv_pairs:
            DjangoChunk.objects.update_or_create(
                chunk_id=str(k),
                defaults={
                    'text_content': v.page_content,
                    'metadata': v.metadata,
                    'in_byte_store': True
                }
            )
            
    def mdelete(self, keys):
        str_keys = [str(k) for k in keys]
        DjangoChunk.objects.filter(chunk_id__in=str_keys).update(in_byte_store=False)
        
    def yield_keys(self):
        return [str(i) for i in DjangoChunk.objects.filter(in_byte_store=True).values_list('chunk_id', flat=True)]

class RAGService:

    db = None
    embeddings = None
    hashes_indexed = {}
    store = None
    retriever = None
    chain = None

    def __init__(self, collection_name="verbal_background_resources"):
        self.collection_name = collection_name
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")
        self.reading_ids = set()  # reading_ids
        self.store = DjangoChunkStore()
        self.id_key = "chunk_id"

        # Build the SQLAlchemy connection string from Django's Postgres settings
        db_config = settings.DATABASES['default']
        user = db_config.get('USER', '')
        password = db_config.get('PASSWORD', '')
        host = db_config.get('HOST', '127.0.0.1')
        port = db_config.get('PORT', '5432')
        db_name = db_config.get('NAME', '')
        self.connection_string = f"postgresql+psycopg://{user}:{password}@{host}:{port}/{db_name}"

        self.engine = create_engine(self.connection_string)

        self.db = PGVector(
            embeddings=self.embeddings,
            collection_name=self.collection_name,
            connection=self.engine,
            use_jsonb=True,
        )
        
        # Pre-populate indexed hashes from Django
        for chunk in DjangoChunk.objects.filter(in_vector_index=True):
            scheme = chunk.metadata.get('indexed_hash')
            if scheme:
                if scheme not in self.hashes_indexed:
                    self.hashes_indexed[scheme] = []
                self.hashes_indexed[scheme].append(chunk.chunk_id)

        # initialise summary generator and glossary generator

        logger.info(f'RAG Service db initialized. {self.db}')
        if not os.path.exists("index_dump.txt"):
            self.dump_index_to_file()

    def force_reindex_all(self):
        """Resets the vector index status and re-indexes all chunks. Useful for test fixture loads."""
        DjangoChunk.objects.all().update(in_vector_index=False)
        self.hashes_indexed = {}
        self.index_unindexed_chunks()

    def index_unindexed_chunks(self):
        """Indexes any RAGChunks in Postgres that are not yet in the PGVector index."""
        unindexed = DjangoChunk.objects.filter(in_vector_index=False)
        if not unindexed.exists():
            return
            
        lc_docs_to_add = []
        chunk_ids = []
        
        for chunk in unindexed:
            raw_doc = LangchainDocument(page_content=chunk.text_content or "", metadata=chunk.metadata)
            lc_docs_to_add.append(raw_doc)
            chunk_ids.append(str(chunk.chunk_id))
            
            scheme = chunk.metadata.get('indexed_hash')
            if scheme:
                if scheme not in self.hashes_indexed:
                    self.hashes_indexed[scheme] = []
                if str(chunk.chunk_id) not in self.hashes_indexed[scheme]:
                    self.hashes_indexed[scheme].append(str(chunk.chunk_id))
                    
        if lc_docs_to_add:
            logger.info(f'Indexing {len(lc_docs_to_add)} chunks into PGVector in batches...')
            batch_size = 500
            for i in range(0, len(lc_docs_to_add), batch_size):
                batch_docs = lc_docs_to_add[i:i + batch_size]
                batch_ids = chunk_ids[i:i + batch_size]
                logger.info(f'Indexing batch {i // batch_size + 1} of {len(lc_docs_to_add) // batch_size + 1}...')
                self.db.add_documents(batch_docs, ids=batch_ids)
                
            unindexed.update(in_vector_index=True)
            logger.info('Indexing complete.')

    def save_db(self):
        pass # Postgres persists automatically

    def load_db(self):
        pass # Postgres persists automatically

    def disconnect(self):
        """Closes SQLAlchemy connection pools to prevent database locks during test teardown."""
        if hasattr(self, 'engine') and self.engine:
            self.engine.dispose()

    def delete_document_from_vectorstore(self, document):
        reading_list = document.readingstrategy_set.all()
        for reading in reading_list:
            self.delete_reading_from_vectorstore(reading)

    def delete_reading_from_vectorstore(self, readingstrategy):
        # Only delete chunks if they are not used by any OTHER reading strategy
        # The post_delete signal on StrategyChunkUsage will handle the reference counting
        # and delete the RAGChunk + Store content if orphaned.
        readingstrategy.usages.all().delete()
        logger.info(f'Deleted readings of {readingstrategy.document.title}.')

    def audit_stores(self):
        """
        Finds documents that have not been ingested.
        """
        unindexed_docs = DjangoDocument.objects.annotate(
            chunk_count=Count('readingstrategy__usages')
        ).filter(chunk_count=0).distinct()
        
        return {
            "unindexed_docs": unindexed_docs,
            "has_issues": unindexed_docs.exists()
        }

    def get_direct_context(self, query, k=1):
        retrieved_docs = self.db.similarity_search(query, k=k)  # Get top result page
        doc_cards = [f"file {i}:" +doc.metadata["filename"] + ": " + doc.page_content for i, doc in enumerate(retrieved_docs)]
        logger.info(f'Retrieved context: {doc_cards}')
        retrieved_context =  "Also, this is an arguably relevant excerpt from my document library:" + "\n".join(doc_cards)
        return retrieved_context

    def get_chunk_from_store(self, chunk_id):
        return self.store.mget([chunk_id])[0]

    def get_context(self, query: str, k: int = 4, max_distance: float = 1.5) -> List[LangchainDocument]:
        """
        Retrieves relevant documents by searching the vector index and fetching parent chunks.

        Uses PGVector distance (lower = better). Results beyond max_distance are
        dropped before the lexical relevance check to eliminate noise.
        """
        docs_and_scores = self.db.similarity_search_with_score(query, k=k*2)

        if not docs_and_scores:
            logger.info('Matches: []')
            return []

        # Gate out results that exceed the distance threshold (Finding 1.1)
        matches = [(doc, score) for doc, score in docs_and_scores if score <= max_distance]
        if not matches:
            logger.info(f'All {len(docs_and_scores)} results exceeded max_distance={max_distance}')
            return []

        logger.info(f'Matches: {len(matches)}/{len(docs_and_scores)} passed distance gate (max={max_distance})')

        parent_ids = []
        seen_ids = set()

        for doc, _score in matches:
            meta = doc.metadata or {}
            # Safely extract chunk_id from metadata or fallback to document ID
            p_id = meta.get(self.id_key) or meta.get("id") or getattr(doc, 'id', None)

            if p_id:
                p_id = str(p_id)
                if p_id not in seen_ids:
                    parent_ids.append(p_id)
                    seen_ids.add(p_id)

            if len(parent_ids) >= k * 2:
                break
        logger.info(" ".join([str(x) for x in ['ParentIDs', parent_ids]]))

        final_docs = []
        if parent_ids:
            try:
                results = self.store.mget(parent_ids)
                final_docs = [doc for doc in results if doc is not None]
            except Exception as e:
                logger.info(f'Error during manual store retrieval: {e}')

        scored_results = self.verify_rag_relevance(query, final_docs)
        
        top_results = scored_results[:k]
        sorted_docs = [doc for doc, score in top_results]

        final_parent_ids = [str(doc.metadata.get(self.id_key)) for doc in sorted_docs if doc.metadata and doc.metadata.get(self.id_key)]
        logger.info(" ".join([str(x) for x in ['Final Parent IDs', final_parent_ids]]))
        if final_parent_ids:
            DjangoChunk.objects.filter(chunk_id__in=final_parent_ids)\
                .update(hit_count=models.F('hit_count') + 1, last_accessed=timezone.now())

        if sorted_docs:
            response_preview = "\n\n".join([f"[{i+1}] {d.page_content[:300]}..." for i, d in enumerate(sorted_docs)])
            RAGQueryLog.objects.create(
                query_text=query,
                response_generated=response_preview
            )

        return sorted_docs


    def load_models(self):
        """
        Syncs the vector store with the database and saves it to disk.
        Creates an empty store if one doesn't exist and no documents are found.
        """
        from llm_api.apps import service_registry
        ai_service = service_registry.ai_service

        logger.info(f'RAG Service Models Loaded.')

    @staticmethod
    def is_likely_toc(text_chunk: str) -> bool:
        lines = text_chunk.split('\n')
        if not lines:
            return False

        # Filter empty lines to avoid skewing percentages
        non_empty_lines = [line.strip() for line in lines if line.strip()]
        if not non_empty_lines:
            return False

        # 1. "Dots" pattern: Looks for "...... 12" common in old school PDFs
        # Added check that it must match at least 2 distinct lines to avoid a fluke
        dot_pattern = re.compile(r'\.{3,}\s*\d+$')
        dot_matches = sum(1 for line in non_empty_lines if dot_pattern.search(line))

        if dot_matches > 2:
            return True

        # 2. Header Check: Look for variations of "Contents", "Index", "Figures"
        # We limit this to the first 5 non-empty lines
        header_pattern = re.compile(r'^\s*(table of|list of)?\s*(contents|index|figures|tables)\b', re.IGNORECASE)
        has_structural_header = any(header_pattern.match(line) for line in non_empty_lines[:5])

        # 3. Page Number Heuristic
        # Refined Regex: \s\d{1,3}$
        # Matches " 5", " 102". Ignores " 2024" (Year) or " 10000" (Data)
        page_num_pattern = re.compile(r'\s\d{1,3}$')
        lines_ending_in_page_num = sum(1 for line in non_empty_lines if page_num_pattern.search(line))

        # Calculate ratio based on non-empty lines
        ratio = lines_ending_in_page_num / len(non_empty_lines)

        # Decision: Requires BOTH a header AND a pattern of numbers
        if has_structural_header and ratio > 0.25:
            return True

        # Special Case: If NO header, but massive signal (e.g., > 60% of lines look like TOC), toss it.
        # This catches TOCs that span multiple pages where the "Contents" header was on the previous page.
        if ratio > 0.6:
            return True

        return False

    def convert_chunk_store_document(self, document: DjangoDocument, chunk_size=None, chunk_overlap=None) -> Tuple[List[LangchainDocument], List[str]]:
        # ... inside the loop for docs_to_add ...
        
        # Determine effective chunking parameters
        eff_size = chunk_size if chunk_size is not None else document.chunk_size
        eff_overlap = chunk_overlap if chunk_overlap is not None else document.chunk_overlap
        
        current_scheme = document.chunking_scheme(eff_size, eff_overlap)
        
        if current_scheme in self.hashes_indexed:
            # Check if the chunks actually exist in the store (integrity check)
            existing_ids = self.hashes_indexed[current_scheme]
            if existing_ids and self.store.mget([existing_ids[0]])[0] is not None:
                logger.info(f'Reusing {len(existing_ids)} existing chunks for scheme {current_scheme}')
                return [], existing_ids
            else:
                logger.info(f'Scheme {current_scheme} found in index but chunks missing from store. Re-indexing.')

        raw_docs = []
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=eff_size,
            chunk_overlap=eff_overlap,
            separators=["\n\n", "\n", " ", ""]) # Tries to split by paragraph, then line, then word)

        file_path = document.file.path
        _, file_extension = os.path.splitext(file_path)
        if file_extension.lower() in ['.txt', '.md']:
            with document.file.open('r') as f:
                # Wrap raw text in a Document object to match Loader outputs
                raw_docs = [LangchainDocument(page_content=f.read(), metadata={"source": file_path})]

        elif file_extension.lower() == '.pdf':
            # PyPDFLoader returns 1 Document per page
            loader = PyPDFLoader(file_path)
            raw_docs = loader.load()
            for doc in raw_docs:
                if 'page' in doc.metadata:
                    doc.metadata['page_number'] = doc.metadata['page'] + 1

        elif file_extension.lower() == '.docx':
            # Docx2txtLoader usually returns 1 Document for the WHOLE file and pagination is not available
            loader = Docx2txtLoader(file_path)
            raw_docs = loader.load()

        elif file_extension.lower() == '.pptx':
            loader = UnstructuredPowerPointLoader(file_path)
            raw_docs = loader.load()
            # Unstructured often provides 'page_number', but let's ensure it exists
            for i, doc in enumerate(raw_docs):
                if 'page_number' not in doc.metadata:
                    doc.metadata['page_number'] = i + 1

        elif file_extension.lower() == '.zip':
            # 1. Define Extraction Path: media/corpora/<hash>
            extract_path = os.path.join(settings.MEDIA_ROOT, "corpora", document.indexed_hash)
            
            # 2. Unzip if not already there (Idempotency check)
            if not os.path.exists(extract_path):
                logger.info(f'Unzipping corpus to {extract_path}...')
                with zipfile.ZipFile(file_path, 'r') as zip_ref:
                    zip_ref.extractall(extract_path)
            
            # 3. Load using DirectoryLoader (Iterates all HTML files)
            # We use BSHTMLLoader to parse the HTML into text
            loader = DirectoryLoader(
                extract_path, 
                glob="**/*.html", 
                loader_cls=BSHTMLLoader,
                loader_kwargs={"open_encoding": "utf-8"}
            )
            raw_docs = loader.load()

        elif file_extension.lower() == '.ipynb':
            # Native parsing preserves code vs markdown cells perfectly for LLMs
            loader = NotebookLoader(
                file_path,
                include_outputs=True,       # Includes cell outputs (prints, errors)
                max_output_length=500,      # Truncates massive outputs (like giant matrices)
                remove_newline=False
            )
            raw_docs = loader.load()
            for i, doc in enumerate(raw_docs):
                if 'page_number' not in doc.metadata:
                    doc.metadata['page_number'] = i + 1

        else:
            logger.info('Unsupported file type.')
            
        # Normalize line endings for all loaded docs to ensure splitters and regexes work safely
        for doc in raw_docs:
            doc.page_content = doc.page_content.replace('\r\n', '\n').replace('\r', '\n')

        final_chunks = [chunk for chunk in text_splitter.split_documents(raw_docs)
                        if not self.is_likely_toc(chunk.page_content)]

        total_chunks = len(final_chunks)
        for index, vec_doc in enumerate(final_chunks):
            # A. Merge Global Metadata (e.g., indexed_hash, filename)
            # We copy to avoid mutating the class reference
            global_meta = document.metadata.copy() if document.metadata else {}
            vec_doc.metadata.update(global_meta)

            # B. Calculate Relative Location (The "Universal" Metric)
            # "chunk_index": 0, "total_chunks": 10
            vec_doc.metadata["chunk_index"] = index
            vec_doc.metadata["total_chunks"] = total_chunks

            # "location_percent": 25 (integer for easy filtering/display)
            if total_chunks > 0:
                vec_doc.metadata["location_percent"] = int(((index + 1) / total_chunks) * 100)
            else:
                vec_doc.metadata["location_percent"] = 0

            # C. Fallback for Page Numbers
            # If a file format didn't provide a page number, we can use the location
            # to give a rough estimate or simply default to 1.
            if "page_number" not in vec_doc.metadata:
                # Optional: Estimate page number for TXT if you didn't do the line-count trick
                # vec_doc.metadata["page_number"] = 1
                vec_doc.metadata["page_number"] = f"{vec_doc.metadata['location_percent']}%"

        chunk_ids = [str(uuid4()) for _ in range(len(final_chunks))]
        #  PREPPED CHUNKS COMPLETE. Load them to
        chunks = final_chunks
        chunks_to_store = []
        lc_docs_to_add = []

        for i, (chunk, chunk_id) in enumerate(zip(chunks, chunk_ids)):
            # Update the chunk's metadata in place so the Store copy gets the ID
            chunk.metadata[self.id_key] = chunk_id
            chunk.metadata["filename"] = document.file.name
            chunk.metadata["chunk_number"] = f"{str(i + 1)}/{str(len(chunks))}"
            chunk.metadata["indexed_hash"] = current_scheme
            
            chunks_to_store.append((chunk_id, chunk))
            
        self.store.mset(chunks_to_store)
        self.hashes_indexed[current_scheme] = chunk_ids
        
        self.index_unindexed_chunks()
        return chunks, chunk_ids

    def convert_chunk_store_document_grobid(self, document: DjangoDocument) -> Tuple[List[LangchainDocument], List[str]]:
        """
        Uses the cached TEI XML from the Grobid client to split the document cleanly by semantic sections.
        """
        current_scheme = f"{document.indexed_hash}-grobid_semantic"
        
        if current_scheme in self.hashes_indexed:
            existing_ids = self.hashes_indexed[current_scheme]
            if existing_ids and self.store.mget([existing_ids[0]])[0] is not None:
                logger.info(f'Reusing {len(existing_ids)} existing Grobid chunks for scheme {current_scheme}')
                return [], existing_ids

        if not hasattr(document, 'grobid_metadata') or not document.grobid_metadata or not document.grobid_metadata.tei_xml:
            raise ValueError(f"Document '{document.title}' does not have cached Grobid TEI XML. Run Grobid extraction first.")

        # Quality check: Reject files that Grobid failed to structure (e.g., PowerPoint PDFs)
        ref = document.grobid_metadata
        meaningful_fields = [
            getattr(ref, 'authors', ''), getattr(ref, 'abstract', ''),
            getattr(ref, 'journal', ''), getattr(ref, 'publisher', ''),
            getattr(ref, 'year', ''), getattr(ref, 'publication_date', ''),
            getattr(ref, 'volume', ''), getattr(ref, 'issue', ''),
            getattr(ref, 'pages', ''), getattr(ref, 'doi', '')
        ]
        
        # If ALL of these fields are empty/falsy, the Grobid extraction is deemed too low-quality
        if not any(str(field).strip() for field in meaningful_fields if field is not None):
            logger.info(f"Skipping Grobid chunking for '{document.title}': No meaningful metadata extracted. Falling back to default splitters.")
            return [], []

        tei_xml = document.grobid_metadata.tei_xml
        from grobid_client.tasks import grobid_tei_to_semantic_chunks
        
        final_chunks = grobid_tei_to_semantic_chunks(tei_xml, document_title=document.title)
        
        total_chunks = len(final_chunks)
        for index, vec_doc in enumerate(final_chunks):
            global_meta = document.metadata.copy() if document.metadata else {}
            vec_doc.metadata.update(global_meta)
            vec_doc.metadata["chunk_index"] = index
            vec_doc.metadata["total_chunks"] = total_chunks
            vec_doc.metadata["location_percent"] = int(((index + 1) / total_chunks) * 100) if total_chunks > 0 else 0
            if "page_number" not in vec_doc.metadata:
                vec_doc.metadata["page_number"] = f"{vec_doc.metadata['location_percent']}%"

        chunk_ids = [str(uuid4()) for _ in range(len(final_chunks))]
        
        chunks_to_store = []
        lc_docs_to_add = []

        for i, (chunk, chunk_id) in enumerate(zip(final_chunks, chunk_ids)):
            chunk.metadata[self.id_key] = chunk_id
            chunk.metadata["filename"] = document.file.name
            chunk.metadata["chunk_number"] = f"{str(i + 1)}/{str(len(final_chunks))}"
            chunk.metadata["indexed_hash"] = current_scheme
            
            chunks_to_store.append((chunk_id, chunk))
            
        if chunks_to_store:
            self.store.mset(chunks_to_store)
            self.hashes_indexed[current_scheme] = chunk_ids
            self.index_unindexed_chunks()
            
        return final_chunks, chunk_ids

    def complete_reading(self, reading_strategy: DjangoReadingStrategy|PromptStrategy|RegexStrategy| AbbreviationsReadingStrategy|GrobidReadingStrategy):
        # Polymorphic call to the consolidated strategy method
        reading_strategy.apply_strategy(self)

    def ingest_queryset_documents(self, queryset=None):
        """This is to be the top function for ingestion and assumes a queryset of our Django Document models.
        It might be worthwhile in other examples to split the queryset by filtering on indexing_strategy"""

        if queryset is None:
            return

        for document in queryset:
            # Safely ensure a default chunking strategy exists without crashing on legacy duplicates
            if not DjangoReadingStrategy.objects.filter(document=document, strategy_description="Default Chunking").exists():
                DjangoReadingStrategy.objects.create(document=document, strategy_description="Default Chunking")
            
            # Auto-create Grobid Semantic Chunking strategy if meaningful TEI XML exists
            if hasattr(document, 'grobid_metadata') and document.grobid_metadata and document.grobid_metadata.tei_xml:
                ref = document.grobid_metadata
                meaningful_fields = [
                    getattr(ref, 'authors', ''), getattr(ref, 'abstract', ''),
                    getattr(ref, 'journal', ''), getattr(ref, 'publisher', ''),
                    getattr(ref, 'year', ''), getattr(ref, 'publication_date', ''),
                    getattr(ref, 'volume', ''), getattr(ref, 'issue', ''),
                    getattr(ref, 'pages', ''), getattr(ref, 'doi', '')
                ]
                if any(str(field).strip() for field in meaningful_fields if field is not None):
                    # Safely ensure Grobid strategy exists without crashing
                    if not GrobidReadingStrategy.objects.filter(document=document, strategy_description="Grobid Semantic Chunking").exists():
                        GrobidReadingStrategy.objects.create(
                            document=document,
                            strategy_description="Grobid Semantic Chunking"
                        )

            # Ingest all types of strategies
            self.ingest_queryset_reading_strategies(document.readingstrategy_set.all())
            self.ingest_queryset_reading_strategies(document.promptstrategy_set.all())
            self.ingest_queryset_reading_strategies(document.regexstrategy_set.all())
            self.ingest_queryset_reading_strategies(document.abbreviationsreadingstrategy_set.all())
            self.ingest_queryset_reading_strategies(document.grobidreadingstrategy_set.all())


    def ingest_queryset_reading_strategies(self, queryset=None):
        """This is to be the top function for ingestion and assumes a queryset of our Django ReadingStrategy models."""

        if queryset is None:
            return

        for readingstrategy in queryset:
            self.complete_reading(readingstrategy)


    def get_chunk_summary(self, chunk_text, custom_prompt=None):
        """This function provides more diverse index options - you can use it to generate hypothetical questions answered in the chunk text, or a summary of the text, or any other response.  Significant danger of hallucination - instead of summaries, you could get the models general knowledge that the chunk "reminded it" of, which is unsourced and potentially false. You may also get summaries that are just wrong. There are also significant reliability issues - the summary output is a longform, a shortform and then keywords, but one or more may be empty, the longform can be shorter than the shortform and so on."""

        if custom_prompt:
            prompt = f"{custom_prompt}\n Chunk: {chunk_text[:2000]}"
        else:
            prompt = f"You are a data ingestion agent. Analyze the following document chunk. \n 1. If it is a Table of Contents, Index, or Copyright page, it is structural noise and no summary is needed.\n 2. A longform response is at least several sentences, and covers many or all of the ideas in the chunk. A shortform response is a sentence or two, covering the key ideas.\n Chunk: {chunk_text[:2000]}" # Truncate for speed if needed

        from llm_api.apps import service_registry
        ai_service = service_registry.ai_service
        
        try:
            summary_result = ai_service.generate_outline(
                messages=prompt,
                response_schema=DocumentHandles,
                max_new_tokens=1500
            )
            if isinstance(summary_result, dict):
                if "error" in summary_result:
                    raise ValueError(summary_result["details"])
                summary = DocumentHandles.model_validate(summary_result)
            elif isinstance(summary_result, str):
                summary = DocumentHandles.model_validate_json(summary_result)
            else:
                summary = summary_result
        except Exception as e:
            logger.info(f'Error generating chunk summary: {e}')
            summary = None

        if summary:
            logger.info(" ".join([str(x) for x in ['Summary obj', summary]]))
            if len(summary.long_form) < len(summary.short_form):
                summary.long_form, summary.short_form = summary.short_form, summary.long_form
            return summary
        return DocumentHandles(long_form=chunk_text[:500], short_form=chunk_text[:100], keywords=["summarisation failed",])

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

    def get_glossary_terms(self, raw_text, regex):
        try:
            # Security Warning: User-supplied regexes can cause ReDoS (Regular Expression Denial of Service).
            # Ensure that only trusted administrators can modify the extraction_regex field.
            regex = re.compile(regex)
            matches = regex.findall(raw_text)
            if matches:
                valid_definitions = []
                for match in matches:
                    # Expecting (term, definition) tuple from regex groups
                    if len(match) >= 2:
                        term = match[0].strip()
                        definition = match[1].strip()
                        valid_definitions.append(GlossaryItem(term=term, definition=definition))

                glossary_extraction = valid_definitions
                return glossary_extraction
        except re.error as e:
            logger.info(f'Invalid regex {regex}: {e}')
            # Fallback to LLM if regex fails? Or just log error?
            pass

        # GlossaryExtraction is a LIST of GlossaryEntry items, so it handles multiple terms per page
    def glossary_generator(self, raw_text):
        """This code attempts to use a Language Model to yield glossary terms and definitions. Slow and pretty unreliable."""
        from llm_api.apps import service_registry
        ai_service = service_registry.ai_service

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
        try:
            result = ai_service.generate_outline(
                messages=prompt,
                response_schema=GlossaryExtraction,
                max_new_tokens=1024
            )
            if isinstance(result, dict):
                if "error" in result:
                    raise ValueError(result["details"])
                glossary_entries = GlossaryExtraction.model_validate(result)
            elif isinstance(result, str):
                glossary_entries = GlossaryExtraction.model_validate_json(result)
            else:
                glossary_entries = result
        except Exception as e:
            logger.info(f'Error generating glossary: {e}')
            glossary_entries = None

        logger.info(" ".join([str(x) for x in ['Glossary from chunks:', result]]))

        if glossary_entries:
            valid_definitions = []
            for item in glossary_entries.items:
                if self.is_hallucination(item.term, raw_text):
                    logger.info(" ".join([str(x) for x in ['Hallucination detected:', item.term, ' not in', raw_text]]))
                if not self.check_definition_grounding(item.definition, raw_text):
                    logger.info(" ".join([str(x) for x in ['Definition not grounding:', item.definition, ' not found in', raw_text]]))
                if not self.is_hallucination(item.term, raw_text) and self.check_definition_grounding(item.definition, raw_text):
                    valid_definitions.append(item)

            return valid_definitions
        return []

    def verify_rag_relevance(self, user_query, retrieved_chunks, min_overlap=0.1):
        from llm_api.apps import service_registry
        nlp_service = service_registry.nlp_service

        # 1. Get core concepts from query
        # Query: "How do I fix memory leaks?" -> {'fix', 'memory', 'leak'}
        # Lowercase lemmas to ensure case-insensitive matching (e.g. "Water" vs "water")
        query_lemmas = set([t.lower() for t in nlp_service.get_lemmatized_tokens(user_query)])

        scored_results = []
        for i, chunk in enumerate(retrieved_chunks):
            # 2. Get concepts from the chunk
            content_to_check = chunk.page_content
            if chunk.metadata.get("original_term"):
                content_to_check = chunk.metadata["original_term"] +": " + content_to_check

            chunk_lemmas = set([t.lower() for t in nlp_service.get_lemmatized_tokens(content_to_check)])

            # 3. Calculate overlap
            # Calculate Query Coverage: What percentage of the query's concepts are present in the chunk?
            if len(query_lemmas) == 0:
                overlap_ratio = 0.0
            else:
                intersection = query_lemmas.intersection(chunk_lemmas)
                base_overlap = len(intersection) / len(query_lemmas)
                
                # Length Penalty (Information Density):
                # Discount overlap score for excessively long chunks.
                # Chunks under 100 lemmas get no penalty.
                # A 1000-lemma chunk is penalized heavily (100/1000 = 0.1).
                length_penalty = min(1.0, 100.0 / max(1, len(chunk_lemmas)))
                overlap_ratio = base_overlap * length_penalty
            
            # 4. Tie-breaker: Prefer definitions ONLY if they actually match the query context
            is_relevant_definition = 0
            original_term = chunk.metadata.get("original_term")
            if original_term:
                term_lemmas = set([t.lower() for t in nlp_service.get_lemmatized_tokens(original_term)])
                # If the term shares words with the query, or the definition is a strong match
                if term_lemmas.intersection(query_lemmas) or overlap_ratio > 0.5:
                    is_relevant_definition = 1
            
            # By defaulting to 0.0, we stop punishing the semantic embedding model for finding synonyms!
            if overlap_ratio >= min_overlap or is_relevant_definition:
                scored_results.append((chunk, overlap_ratio, is_relevant_definition, i))

        # HYBRID SEARCH SORTING:
        # 1. Target Definition (Exact Glossary Hit)
        # 2. Lexical Overlap (Safeguard against embedding hallucinations like Python code)
        # 3. Original Semantic Rank (-x[3])
        sorted_results = sorted(scored_results, key=lambda x: (x[2], x[1], -x[3]), reverse=True)
        return [(item[0], item[1]) for item in sorted_results]


    def dump_index_to_file(self, output_filename="index_dump.txt", comparison_doc_id=None):
        """
        Writes the entire contents of the vector store + byte store to a text file.
        Format:
        [TERM/SUMMARY] -> [FULL CONTENT] (Source)
        """
        from background_resources.models import Document
        indexed_false_positives = []  # hallucinated entries
        indexed_misses = []           #  entries not found in index

        deterministic_glossary_dict = {}
        if comparison_doc_id:
            try:
                d = Document.objects.get(id=comparison_doc_id)
                deterministic_glossary_dict = self.parse_glossary_deterministic(d)
                with open("glossary_read_dump.txt", "w", encoding="utf-8") as f:
                    f.write(f"--- DUMP OF {len(deterministic_glossary_dict.keys())} Deterministic Glossary Dict ITEMS ---\n\n")

                    for i, (key, value) in enumerate(deterministic_glossary_dict.items()):
                        f.write(f"Entry #{i + 1}--------------------------------------------\n")
                        f.write(f"Index Key (Term): {key}")
                        f.write(f"\nDefinition: {value}\n")
                logger.info('dumped dict')
            except Document.DoesNotExist:
                logger.info(f'Comparison document with ID {comparison_doc_id} not found.')

        logger.info(f'Dumping index to {output_filename}...')
        index_docs = DjangoChunk.objects.filter(in_vector_index=True)
        index_dict = {}
        with open(output_filename, "w", encoding="utf-8") as f:
            f.write(f"--- DUMP OF {index_docs.count()} INDEXED ITEMS ---\n\n")

            for i, index_doc in enumerate(index_docs):
                # The text used for searching (e.g. The Glossary Term)
                search_term = (index_doc.text_content or "").replace("\n", " ")

                # The ID pointing to the full content
                chunk_id = index_doc.chunk_id

                # Retrieve the full content (e.g. The Definition)
                # We use the store directly
                docs = self.store.mget([chunk_id])
                full_content_doc = docs[0] if docs else None

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

        logger.info('Dumped vector_store.')

        if deterministic_glossary_dict:
            for key, value in deterministic_glossary_dict.items():
                if index_dict.get(key) is None:
                    indexed_misses.append(key)

            logger.info(f'Found {len(indexed_misses)} misses / {len(deterministic_glossary_dict.keys())} and ')
            logger.info(f'{len(indexed_false_positives)} hallucinations / {len(index_dict)} total.')
            logger.info(" ".join([str(x) for x in ['Index Misses:', '\n-'.join(indexed_misses)]]))
            logger.info(" ".join([str(x) for x in ['Index Hallucinations:', '\n-'.join(indexed_false_positives)]]))