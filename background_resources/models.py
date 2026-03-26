


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
import shutil
import re
from uuid import uuid4
from django.db import models
from django.db.models.signals import pre_delete, pre_save, post_save, post_delete
from django.dispatch import receiver
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.utils import timezone


# You will need to have these libraries installed:
# pip install langchain langchain-community faiss-cpu sentence-transformers
from langchain.docstore.document import Document as LangchainDocument


class Document(models.Model):
    """
    A model to store uploaded documents, their content hash, and manage
    their inclusion in a FAISS vector store.
    """

    title = models.CharField(max_length=255)
    # Citation / Provenance Information
    author = models.CharField(max_length=255, blank=True, null=True, help_text="Original author or organization")
    publication_date = models.DateField(blank=True, null=True)
    citation_text = models.TextField(blank=True, null=True, help_text="APA/MLA citation string")
    source_url = models.URLField(blank=True, null=True)
    
    file = models.FileField(upload_to='documents/')

    # --- Indexing

    indexed_hash = models.TextField(null=True, blank=True, editable=False, unique=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    currently_indexed = models.BooleanField(default=False)
    metadata = models.JSONField(null=True, blank=True, default=dict)
    chunk_size = models.IntegerField(default=1000)
    chunk_overlap = models.IntegerField(default=20)

    def __str__(self):
        return self.title

    def chunking_scheme(self, override_size=None, override_overlap=None):
        # Allows calculating scheme for specific strategies
        size = override_size if override_size is not None else self.chunk_size
        overlap = override_overlap if override_overlap is not None else self.chunk_overlap
        return (f"{self.indexed_hash}-{size}_{overlap}")

    def validate_current_index(self):
        if self.currently_indexed:
            return self.metadata.get("chunking_scheme", "") == self.chunking_scheme()
        return self.currently_indexed

    def save(self, *args, **kwargs):
        """Calculates the SHA256 hash of the uploaded file."""
        updated_file = False
        hasher = hashlib.sha256()
        f = self.file.open('rb')
        f.seek(0)
        for chunk in iter(lambda: f.read(4096), b""):
            hasher.update(chunk)
        if self.indexed_hash != hasher.hexdigest():
            # File content has changed. Recalculating hash
            self.indexed_hash = hasher.hexdigest()
            updated_file = True

        self.metadata["indexed_hash"] = self.indexed_hash
        self.metadata["filename"] = self.file.name
        
        if updated_file:
            self.currently_indexed = False

        if self.metadata.get("chunking_scheme", "") != self.chunking_scheme():
            self.currently_indexed = False

        super().save(*args, **kwargs)


@receiver(pre_delete, sender=Document)
def delete_document_files(sender, instance, **kwargs):
    """
    Signal to delete files from FAISS and file storage
    *before* the Document object is deleted from the database.
    """
    print(f"Pre-delete signal for {instance.title} (hash: {instance.indexed_hash})...")
    from llm_api.apps import service_registry
    rag_service = service_registry['rag_service']

    # 1. Delete the extracted corpus folder if it exists
    from django.conf import settings
    corpus_path = os.path.join(settings.MEDIA_ROOT, "corpora", instance.indexed_hash)
    if os.path.exists(corpus_path):
        shutil.rmtree(corpus_path)
        print(f"Deleted corpus at {corpus_path}")

    # 2. Delete the actual file (e.g., the PDF/TXT/ZIP) from storage
    if instance.file:
        instance.file.delete(save=False)  # 'save=False' prevents re-saving

@receiver(pre_save, sender=Document)
def pre_save_handler(sender, instance, **kwargs):
    pass

@receiver(post_save, sender=Document)
def ensure_default_reading_strategy(sender, instance, created, **kwargs):
    if created:
        ReadingStrategy.objects.create(document=instance, strategy_description="Default Chunking")


class VectorIndexExplorer(Document):
    # this model is for the admin interface that allows users to explore the vector index
    class Meta:
        proxy = True
        verbose_name = "Vector Index Explorer"
        verbose_name_plural = "Vector Index Explorer"


# TODO: Multi-reading: 
#    - amend doc ingestion to identify indexed terms and chunks by reading_id, rather than chunk_id
#    - also change rag_service.index_hashes
#    - alternatively additional RAG_Service() instantiations for differing purposes.

class RAGChunk(models.Model):
    """
    Registry of all content chunks in the system. Single Source of Truth.
    """
    chunk_id = models.CharField(max_length=36, unique=True, db_index=True)
    text_content = models.TextField(null=True, blank=True)
    metadata = models.JSONField(default=dict)

    # Explicit Storage Status
    in_vector_index = models.BooleanField(default=False, help_text="Is this chunk indexed in FAISS?")
    in_byte_store = models.BooleanField(default=False, help_text="Is the full object stored in the ByteStore?")

    # Usage Stats
    hit_count = models.IntegerField(default=0)
    last_accessed = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.chunk_id} ({self.text_content[:20]}...)"


class StrategyChunkUsage(models.Model):
    """
    Links a RAGChunk to a Strategy (Polymorphic).
    """
    chunk = models.ForeignKey(RAGChunk, on_delete=models.CASCADE, related_name='usages')

    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE)
    object_id = models.UUIDField()
    content_object = GenericForeignKey('content_type', 'object_id')

    class Role(models.TextChoices):
        REUSED = 'REUSED', 'Reused (Reused Store Content)'
        CLIPPED = 'CLIPPED', 'Clipped (Verbatim Document Content)'
        CREATED = 'CREATED', 'Created (Synthetic / Semantic Document Content)'

    role = models.CharField(max_length=20, choices=Role.choices)

    class Meta:
        unique_together = ('chunk', 'content_type', 'object_id', 'role')

class ReadingStrategy(models.Model):
    # These are reusable strategies that can be applied to any document 
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    document_chunking = models.CharField(max_length=255, blank=True, null=True)
    strategy_description = models.CharField(max_length=255)
    
    # Overrides for specific reading strategies (e.g. Small Fragments vs Big Blocks)
    chunk_size_override = models.IntegerField(null=True, blank=True, help_text="If set, overrides document default")
    chunk_overlap_override = models.IntegerField(null=True, blank=True)
    usages = GenericRelation('StrategyChunkUsage')
    output_role = StrategyChunkUsage.Role.CLIPPED

    def __str__(self):
        return f"{self.document.title} - {self.strategy_description}"

    def read_document(self, rag_service_inject):
        # Pass the overrides to the service
        chunks, chunk_ids = rag_service_inject.convert_chunk_store_document(
            self.document, 
            chunk_size=self.chunk_size_override, 
            chunk_overlap=self.chunk_overlap_override
        )
        
        # If chunks are reused (chunks is empty but chunk_ids is not), fetch them from store
        if not chunks and chunk_ids:
            chunks = rag_service_inject.store.mget(chunk_ids)
        for chunk_id, chunk in zip(chunk_ids, chunks):
            rag_chunk, _ = RAGChunk.objects.get_or_create(
                chunk_id=chunk_id,
                defaults={
                    'text_content': chunk.page_content if chunk else "",
                    'metadata': chunk.metadata if chunk else {},
                    'in_vector_index': True,
                    'in_byte_store': True
                }
            )
            StrategyChunkUsage.objects.create(chunk=rag_chunk, content_object=self,
                                              role=StrategyChunkUsage.Role.CLIPPED)
        print(f"{self.__class__.__name__}[{self.id}] logged {len(chunk_ids)} usages to db.")


    def get_chunk_ids(self):
        return self.usages.values_list('chunk__chunk_id', flat=True)

    def apply_strategy(self, rag_service, force=False, source_chunks=None):
        """
        Consolidated strategy execution:
        1. Ensures the document is chunked (base reading).
        2. Extracts derived content (summary, terms, etc.) from those chunks.
        3. Stores derived content in Vector Store and/or Byte Store.
        4. Logs new chunks to the database.
        """
        # 1. Base Strategy: Perform the actual chunking
        indexed_hash = self.document.chunking_scheme(self.chunk_size_override, self.chunk_overlap_override)
        if force or self.document.indexed_hash != indexed_hash or self.usages.count() == 0:
            self.read_document(rag_service_inject=rag_service)
        # Base strategies are done after reading; they don't extract further content from themselves.
        return

    def extract_content(self, chunk, rag_service):
        return []


class AbstractHigherOrderStrategy(models.Model):
    """
    Abstract base class for strategies that extract content from existing chunks
    (e.g. Regex, Prompt, Abbreviations).
    """
    id = models.UUIDField(primary_key=True, default=uuid4, editable=False)
    document = models.ForeignKey(Document, on_delete=models.CASCADE)
    strategy_description = models.CharField(max_length=255)
    
    # Overrides: If set, we generate a transient set of chunks to read from.
    # If not set, we read from the Document's Default ReadingStrategy.
    chunk_size_override = models.IntegerField(null=True, blank=True, help_text="If set, re-chunks document for this reading")
    chunk_overlap_override = models.IntegerField(null=True, blank=True)
    
    usages = GenericRelation('StrategyChunkUsage')
    output_role = StrategyChunkUsage.Role.CLIPPED

    class Meta:
        abstract = True

    def __str__(self):
        return f"{self.document.title} - {self.strategy_description}"

    def get_chunk_ids(self):
        return self.usages.values_list('chunk__chunk_id', flat=True)

    def apply_strategy(self, rag_service, source_chunks=None, ):
        """
        Applies the higher-order strategy:
        1. Identifies source chunks (Default, Override, or provided).
        2. Extracts content.
        3. Saves output to Vector/Byte store and logs to Chunk table.
        """
        chunks = []
        chunk_ids = []

        if source_chunks:
            # Case: Benchmarking or manual injection
            chunks = source_chunks
            chunk_ids = [c.metadata.get(rag_service.id_key, str(uuid4())) for c in chunks]
        
        elif self.chunk_size_override or self.chunk_overlap_override:
            # Case: Override. Generate transient chunks.
            # We don't save these "source" chunks to DB, we just use them for extraction.
            chunks, chunk_ids = rag_service.convert_chunk_store_document(
                self.document,
                chunk_size=self.chunk_size_override,
                chunk_overlap=self.chunk_overlap_override
            )
            # If reused/cached in store
            if not chunks and chunk_ids:
                chunks = rag_service.store.mget(chunk_ids)
                chunks = [c for c in chunks if c]
        
        else:
            # Case: Default. Use the Default ReadingStrategy's chunks.
            default_strat = ReadingStrategy.objects.filter(
                document=self.document, 
                strategy_description="Default Chunking"
            ).first()
            
            if not default_strat:
                # Auto-repair if missing
                default_strat = ReadingStrategy.objects.create(document=self.document, strategy_description="Default Chunking")
            
            # Ensure default is populated
            if default_strat.usages.count() == 0:
                default_strat.read_document(rag_service_inject=rag_service)
            
            chunk_ids = default_strat.get_chunk_ids()
            chunks = [c for c in rag_service.store.mget(chunk_ids) if c]
        
        vectors_to_add = []
        store_docs_to_add = []
        vector_ids = []
        all_generated_ids = []
        id_to_content = {}
        id_to_metadata = {}

        for chunk_id, chunk in zip(chunk_ids, chunks):
            if not chunk: continue

            # We check if we already have a usage for this chunk to avoid duplicates
            if not self.usages.filter(chunk__chunk_id=chunk_id).exists():
                # Ensure RAGChunk exists (it should)
                rag_chunk, _ = RAGChunk.objects.get_or_create(
                    chunk_id=chunk_id,
                    defaults={
                        'text_content': chunk.page_content,
                        'metadata': chunk.metadata,
                        'in_vector_index': True,
                        'in_byte_store': True
                    }
                )
                StrategyChunkUsage.objects.create(
                    chunk=rag_chunk,
                    content_object=self,
                    role=StrategyChunkUsage.Role.REUSED
                )
            # 3. Extract Derived Content (Polymorphic)
            extracted_items = self.extract_content(chunk, rag_service)
            
            for item in extracted_items:
                # Prepare Metadata
                base_metadata = chunk.metadata.copy()
                base_metadata.update(item.get('metadata', {}))
                base_metadata["reading"] = str(self.id)
                base_metadata["strat_type"] = self.__class__.__name__
                base_metadata["read_from"] = chunk_id
                
                # Determine Target (Store Doc or Original Chunk)
                if item.get('store_text'):
                    # Case: New content for store (e.g. Definition)
                    store_id = str(uuid4())
                    in_byte_store = True
                    in_vector_index = False
                    
                    # Ensure the stored document knows its own ID for hit counting
                    store_metadata = base_metadata.copy()
                    store_metadata[rag_service.id_key] = store_id
                    
                    store_doc = LangchainDocument(
                        page_content=item['store_text'],
                        metadata=store_metadata
                    )
                    store_docs_to_add.append((store_id, store_doc))
                    target_id = store_id
                    rag_chunk, _ = RAGChunk.objects.get_or_create(
                        chunk_id=store_id,
                        defaults={
                            'text_content': item['store_text'],
                            'metadata': store_metadata,
                            'in_vector_index': False,
                            'in_byte_store': True
                        }
                    )
                    StrategyChunkUsage.objects.create(chunk=rag_chunk, content_object=self, role=self.output_role)

                else:
                    # Case: Vector points to original chunk (e.g. Summary points to Content)
                    target_id = chunk_id

                # Create Vector Document
                vector_id = str(uuid4())
                vector_ids.append(vector_id)
                all_generated_ids.append(vector_id)

                vector_metadata = base_metadata.copy()
                vector_metadata[rag_service.id_key] = target_id
                id_to_content[vector_id] = item['vector_text']
                id_to_metadata[vector_id] = vector_metadata

                vector_doc = LangchainDocument(
                    page_content=item['vector_text'],
                    id=vector_id,
                    metadata=vector_metadata
                )
                vectors_to_add.append(vector_doc)
                # Register RAGChunk for the Vector Item
                rag_chunk, _ = RAGChunk.objects.get_or_create(
                    chunk_id=vector_id,
                    defaults={
                        'text_content': item['vector_text'],
                        'metadata': vector_metadata,
                        'in_vector_index': True,
                        'in_byte_store': False
                    }
                )
                StrategyChunkUsage.objects.create(chunk=rag_chunk, content_object=self, role=self.output_role)

        # 4. Batch Save
        if vectors_to_add:
            rag_service.db.add_documents(vectors_to_add, ids=vector_ids)
        
        if store_docs_to_add:
            rag_service.store.mset(store_docs_to_add)


    def extract_content(self, chunk, rag_service):
        return []


class PromptStrategy(AbstractHigherOrderStrategy):
    """PromptStrategy uses a custom prompt to an LLM with the chunk to generate key text -
       perhaps a summary, perhaps keywords, perhaps hypothetical questions"""
    prompt = models.TextField(default="Prompt to use the AI service to generate key text to find this chunk - summaries, keywords, hypothetical questions etc. The chunk text is appended to this prompt with whatever intro you put in this prompt.")
    output_role = StrategyChunkUsage.Role.CREATED

    def get_query(self, chunk):
        return f"{self.prompt} \n {chunk.page_content}"

    def extract_content(self, chunk, rag_service):
        # chunk summary returns DocumentRepresentation objects which are long_form, short_form, keywords
        doc_repr = rag_service.get_chunk_summary(chunk.page_content, custom_prompt=self.prompt)
        
        # Check if we got a valid object (not a fallback string) and it has a summary
        if not isinstance(doc_repr, str) and doc_repr.short_form:
            return [{'vector_text': doc_repr.short_form, 'store_text': None}]
        return []

class RegexStrategy(AbstractHigherOrderStrategy):
    # used for basic glossaries - very focused captured text
    # (.+?)(?:\s*:(?!\/\/)\s*|\s+-\s+)(.+)
    strategy_details = models.TextField(
        default="The default glossary grabber - splits lines at colon or hyphen, captures two groups, the indexed text and the corresponding body. Your regex must capture two groups.")
    regex = models.TextField(default=r"(.+?)(?:\s*:(?!\/\/)\s*|\s*-\s+)([^\n\r]+)")
    output_role = StrategyChunkUsage.Role.CLIPPED

    def extract_content(self, chunk, rag_service):
        result = rag_service.get_glossary_terms(chunk.page_content, regex=self.regex)
        items = []
        if result:
            for item in result:
                items.append({
                    'vector_text': item.term,
                    'store_text': f"{item.term}-{item.definition}",
                    'metadata': {'original_term': item.term}
                })
        return items

class AbbreviationsReadingStrategy(AbstractHigherOrderStrategy):
    """ reading for inline definitions of acronyms.  Uses Spacy and scispacy.abbreviation"""
    output_role = StrategyChunkUsage.Role.CLIPPED

    def extract_content(self, chunk, rag_service):
        from llm_api.apps import service_registry
        nlp_service = service_registry['nlp_service']
        
        model = nlp_service.get_abbreviation_model()
        doc = model(chunk.page_content)
        items = []
        if doc._.abbreviations:
            for abrv in doc._.abbreviations:
                items.append({
                    'vector_text': abrv.text,
                    'store_text': f"{abrv.text}:{abrv._.long_form.text}",
                    'metadata': {'original_term': abrv.text}
                })
        return items

class ZeroShotLabelReadingStrategy(AbstractHigherOrderStrategy):
    # readings for examples of specific content  TODO: Lot of options in this discipline. May need more parameters, a sub-chunking strategy
    prompt = models.TextField(default="Generate a label for each of these chunks based on the context provided.")
    label_options = models.JSONField(default=dict)

@receiver(post_delete, sender=StrategyChunkUsage)
def delete_single_chunk_vector(sender, instance, **kwargs):
    """
    Individual cleanup: When a specific Chunk row is deleted (e.g. via Admin inline),
    remove just that item from the vector store.
    """
    from llm_api.apps import service_registry
    rag_service = service_registry['rag_service']
    
    # instance is the StrategyChunkUsage that was just deleted.
    # We check if the underlying RAGChunk has any OTHER usages.
    try:
        rag_chunk = instance.chunk
    except RAGChunk.DoesNotExist:
        # Chunk already deleted (likely by a cascade or previous signal), nothing to do.
        print(f"RAGChunk DID NOT EXIST FOR {instance}")
        return
    
    if not rag_chunk.usages.exists():
        print(f"Cleaning up orphaned chunk {rag_chunk.chunk_id}...")
        try:
            if rag_chunk.in_vector_index:
                # Check if it's actually in the index before trying to delete to avoid errors
                if rag_chunk.chunk_id in rag_service.db.docstore._dict:
                    rag_service.db.delete([rag_chunk.chunk_id])
            
            if rag_chunk.in_byte_store:
                rag_service.store.mdelete([rag_chunk.chunk_id])
            
            rag_service.save_db()
            
            # Finally delete the registry entry
            rag_chunk.delete()
            
        except Exception as e:
            print(f"Error deleting chunk {rag_chunk.chunk_id}: {e}")

class RAGQueryLog(models.Model):
    """Stores queries and feedback to calculate system quality"""
    query_text = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)
    user_feedback_score = models.IntegerField(null=True, blank=True, help_text="1 to 5 or -1/1")
    response_generated = models.TextField()

# TODO: there is an issue at least in the dev version - the database contains Document entries for the benchmarking examples,
#  but the VectorIndex still contains content from Firefighting and Fire Prevention which was uploaded previously.