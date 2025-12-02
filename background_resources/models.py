
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
from django.db.models.signals import pre_delete
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
        # Future proofing:
        # DICTIONARY = 'DICT', 'Match query elements to terms in glossary or dictionary and return definition'
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

    def __str__(self):
        return self.title

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
            self.rechunk()
            self.metadata["chunking_scheme"] = f"{self.chunk_size}_{self.chunk_overlap}"
        elif self.metadata.get("chunking_scheme") == f"{self.chunk_size}_{self.chunk_overlap}":
            pass # do not rechunk - file, chuck_scheme have not changed
        elif self.metadata.get("chunking_scheme") is None:
            self.rechunk()
            self.metadata["chunking_scheme"] = f"{self.chunk_size}_{self.chunk_overlap}"
        else:
            self.rechunk()
            self.metadata["chunking_scheme"] = f"{self.chunk_size}_{self.chunk_overlap}"
        # f.seek(0)
        super().save()

    def is_likely_toc(text_chunk: str) -> bool:
        lines = text_chunk.split('\n')
        if not lines:
            return False

        # 1. Check for "dots" pattern (e.g., "Chapter 1 .......... 5")
        dot_pattern = re.compile(r'\.{3,}\s*\d+$')

        # 2. Check for lines ending in numbers (heuristic for page nums)
        #    We check if > 30% of lines end with a number
        lines_ending_in_number = sum(1 for line in lines if re.search(r'\s\d+$', line.strip()))

        # 3. Keyword check (optional, can be risky if strictly applied)
        has_toc_header = any("contents" in line.lower() for line in lines[:3])

        # Decision logic
        if has_toc_header and lines_ending_in_number > len(lines) * 0.2:
            return True

        # Strong signal: dots + numbers
        dot_matches = sum(1 for line in lines if dot_pattern.search(line))
        if dot_matches > 3:
            return True

        return False

    def create_vectorstore_documents(self):
        # ... inside the loop for docs_to_add ...
        docs_to_load = None
        loader = None
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

        elif file_extension.lower() == '.docx':
            # Docx2txtLoader usually returns 1 Document for the WHOLE file
            loader = Docx2txtLoader(file_path)
            raw_docs = loader.load()

        elif file_extension.lower() == '.pptx':
            loader = UnstructuredPowerPointLoader(file_path)
            raw_docs = loader.load()
        else:
            print("Unsupported file type.")
        final_chunks = [chunk for chunk in text_splitter.split_documents(raw_docs)
                        if not self.is_likely_toc(chunk.page_content)]
        # add metadata including content_hash
        for vec_doc in final_chunks:
            vec_doc.metadata = self.metadata
        return final_chunks

    @classmethod
    def fill_vector_store(cls):
        all_db_docs = cls.objects.all()
        from llm_api.apps import service_registry
        rag_service = service_registry['rag_service']
        print("What is in vector_store already and what is not:")
        print("RAG hashes", rag_service.indexed_hashes)
        print("Doc hashes", [doc.content_hash for doc in all_db_docs])
        docs_to_add = [doc for doc in all_db_docs if doc.content_hash not in rag_service.indexed_hashes]
        docs_missing = [hash for hash in rag_service.indexed_hashes if hash not in [doc.content_hash for doc in all_db_docs]]
        for hash in docs_missing:
            rag_service.indexed_hashes.remove(hash)
            rag_service.delete_document_from_vectorstore(hash)

        print(f"these indexed docs are not matched in the database filestore. {docs_missing}")

        if rag_service.db and not docs_to_add:
            print("Vector store is already up to date.")
            return rag_service.db

        print(f"Found {len(docs_to_add)} new or updated documents to add.")

        # --- 4. Process and collect new documents ---
        all_langchain_docs = []

        for doc in docs_to_add:
            # Add the content_hash to metadata for future checks
            langchain_docs = doc.create_vectorstore_documents()
            all_langchain_docs.extend(langchain_docs)

        # --- 5. Add to the vector store and save ---
        if all_langchain_docs:
            # Add to the existing store
            print("Adding new documents to existing vector store...")
            uuids = [str(uuid4()) for _ in range(len(all_langchain_docs))]
            rag_service.db.add_documents(all_langchain_docs, ids=uuids)

            # Save the updated index back to disk
            print(f"Saving updated vector store to '{VECTOR_STORE}'...")
            rag_service.db.save_local(FILES)
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
        rag_service.delete_document_from_vectorstore(self.content_hash)
        # 2. Rechunk and add to vector store
        langchain_docs = self.create_vectorstore_documents()  # pages or chunks of file
        uuids = [str(uuid4()) for _ in range(len(langchain_docs))]
        rag_service.db.add_documents(langchain_docs, ids=uuids)

    @classmethod
    def generate_summaries(cls, queryset):
        print(cls, queryset)
        from llm_api.apps import service_registry
        rag_service = service_registry['rag_service']
        rag_service.add_summaries(queryset)


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









