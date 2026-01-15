
from verbal_config.settings import VECTOR_STORE, FILES

# Create your models here.


# Splitter Name	Splitting Logic	Best For Documents
# CharacterTextSplitter	A single, user-defined character (e.g., \n).	Structured files like your glossary, CSVs, or log files.
# RecursiveCharacterTextSplitter	A prioritized list of separators (e.g., \n\n, \n, ).	General, unstructured text like articles, books, and web pages.
# TokenTextSplitter	The number of LLM tokens.	Precisely managing context window size for a specific model.
# SemanticChunker	Shifts in semantic meaning (topic).	Dense, thematically complex documents like academic papers.
# MarkdownHeaderTextSplitter	The structure of Markdown headers (#, ##).	Documentation files (README.md), knowledge bases.
# CodeTextSplitter	The syntax of a programming language (functions, classes).	Source code files for building RAG systems on codebases.

import hashlib
import os
import re
from uuid import uuid4
from django.db import models
from django.db.models.signals import pre_delete, pre_save
from django.dispatch import receiver

# You will need to have these libraries installed:
# pip install langchain langchain-community faiss-cpu sentence-transformers
from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_text_splitters import CharacterTextSplitter, RecursiveCharacterTextSplitter
from langchain.docstore.document import Document as LangchainDocument
from langchain_community.document_loaders import PyPDFLoader, Docx2txtLoader, UnstructuredPowerPointLoader
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate

class Document(models.Model):
    """
    A model to store uploaded documents, their content hash, and manage
    their inclusion in a FAISS vector store.
    """

    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='documents/')

    # --- Indexing
    class IndexingStrategy(models.TextChoices):
        STANDARD = 'RAW', 'Standard (Match query to similar text)'
        CONCEPTUAL = 'SUM', 'Conceptual (Match query to pre-processed summary of source text)'
        DICTIONARY = 'DIC', 'Match query elements to terms in glossary or dictionary and return definition'

        # Future proofing:
        # HYPOTHETICAL = 'HYP', 'Q&A Optimized'

    # The internal field name
    indexing_strategy = models.CharField(
        max_length=3,
        choices=IndexingStrategy.choices,
        default=IndexingStrategy.STANDARD,
        verbose_name="Search Optimization Mode"  # <--- The UI Label
    )

    content_hash = models.CharField(max_length=64, blank=True, editable=False, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    metadata = models.JSONField(null=True, blank=True, default=dict)
    chunk_size = models.IntegerField(default=1000)
    chunk_overlap = models.IntegerField(default=20)
    currently_indexed = models.BooleanField(default=False)
    
    extraction_regex = models.CharField(
        max_length=512,
        blank=True,
        null=True,
        help_text="Optional regex for extracting terms (group 1) and definitions (group 2). If provided, this overrides LLM extraction."
    )

    def __str__(self):
        return self.title

    def chunking_scheme(self):
        return f"{self.indexing_strategy}_{self.chunk_size}_{self.chunk_overlap}"

    def validate_current_index(self):
        if self.currently_indexed:
            return self.metadata.get("chunking_scheme", "") == self.chunking_scheme()
        return self.currently_indexed

    def save(self):
        """Calculates the SHA256 hash of the uploaded file."""
        updated_file = False
        hasher = hashlib.sha256()
        f = self.file.open('rb')
        f.seek(0)
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
        if self.content_hash != hasher.hexdigest():
            # File content has changed. Recalculating hash
            self.content_hash = hasher.hexdigest()
            updated_file = True

        self.metadata["content_hash"] = self.content_hash
        self.metadata["filename"] = self.file.name
        if updated_file:
            self.currently_indexed = False

        if self.metadata.get("chunking_scheme", "") != self.chunking_scheme():
            self.currently_indexed = False

        super().save()

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

    def convert_and_chunk_document(self):
        # ... inside the loop for docs_to_add ...
        raw_docs = []
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            separators=["\n\n", "\n", " ", ""]) # Tries to split by paragraph, then line, then word)

        file_path = self.file.path
        _, file_extension = os.path.splitext(file_path)
        if file_extension.lower() == '.txt':
            with self.file.open('r') as f:
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
        else:
            print("Unsupported file type.")
        final_chunks = [chunk for chunk in text_splitter.split_documents(raw_docs)
                        if not self.__class__.is_likely_toc(chunk.page_content)]

        total_chunks = len(final_chunks)
        for index, vec_doc in enumerate(final_chunks):
            # A. Merge Global Metadata (e.g., content_hash, filename)
            # We copy to avoid mutating the class reference
            global_meta = self.metadata.copy() if self.metadata else {}
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
                vec_doc.metadata["page_number"] = f"{vec_doc.metadata["location_percent"]}%"

        doc_ids = [str(uuid4()) for _ in range(len(final_chunks))]
        return final_chunks, doc_ids


    @classmethod
    def load_vector_store(cls):
        all_db_docs = cls.objects.all()
        from llm_api.apps import service_registry
        rag_service = service_registry['rag_service']
        print("What is in vector_store already and what is not:")
        print("RAG hashes", rag_service.indexed_hashes)
        print("Doc hashes", [doc.content_hash for doc in all_db_docs])
        docs_to_add = [doc for doc in all_db_docs if doc.content_hash not in rag_service.indexed_hashes]
        docs_missing = [hash for hash in rag_service.indexed_hashes
                        if hash not in
                        [doc.content_hash for doc in all_db_docs]
                        ]
        for hash in docs_missing:
            rag_service.indexed_hashes.remove(hash)
            rag_service.delete_document_from_vectorstore(hash)

        print(f"these indexed docs are not matched in the database filestore. {docs_missing}")

        if rag_service.db and not docs_to_add:
            print("Vector store is already up to date.")
            return rag_service.db

        print(f"Found {len(docs_to_add)} new or updated documents to add. {docs_to_add}")

        # --- 4. Process and collect new documents ---
        all_langchain_docs = []

        # for doc in docs_to_add:
        #     # Add the content_hash to metadata for future checks
        #     rag_service.ingest_document(doc)

        # --- 5. Add to the vector store and save ---
        print(f"Saving updated vector store to '{VECTOR_STORE}'...")
        rag_service.db.save_local(VECTOR_STORE)
        print("Save complete.")

        return rag_service.db

    def rechunk(self):
        """This function allows users to adjust the splitter parameters and rechunk the document.
          e.g. a glassary, with single sentence definitions can be adjusted to shorter chunk sizes
          e.g.2 a longform source like a manual may need a longer chunk size.
          TODO: this should trigger if the chunk size is changed.
          """
        from llm_api.apps import service_registry
        rag_service = service_registry['rag_service']

        # 1. Delete from vector store
        if self.content_hash in rag_service.indexed_hashes:
            rag_service.delete_document_from_vectorstore(self.content_hash)
        # 2. Rechunk and add to vector store
        rag_service.ingest_document(self)


@receiver(pre_delete, sender=Document)
def delete_document_files(sender, instance, **kwargs):
    """
    Signal to delete files from FAISS and file storage
    *before* the Document object is deleted from the database.
    """
    print(f"Pre-delete signal for {instance.title} (hash: {instance.content_hash})...")
    from llm_api.apps import service_registry
    rag_service = service_registry['rag_service']

    # 1. Delete from vector store
    rag_service.delete_document_from_vectorstore(instance.content_hash)

    # 2. Delete the actual file (e.g., the PDF/TXT) from storage
    if instance.file:
        instance.file.delete(save=False)  # 'save=False' prevents re-saving

@receiver(pre_save, sender=Document)
def pre_save_handler(sender, instance, **kwargs):
    pass



class VectorIndexExplorer(Document):
    class Meta:
        proxy = True
        verbose_name = "Vector Index Explorer"
        verbose_name_plural = "Vector Index Explorer"
