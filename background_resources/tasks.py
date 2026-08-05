from celery import shared_task
from .models import Document, ReadingStrategy
from llm_api.apps import service_registry

@shared_task
def task_process_documents(document_ids):
    """Background task to ingest documents."""
    docs = Document.objects.filter(id__in=document_ids)
    service_registry.rag_service.ingest_queryset_documents(docs)

@shared_task
def task_process_reading_strategies(strategy_ids):
    """Background task to execute reading strategies."""
    strategies = ReadingStrategy.objects.filter(id__in=strategy_ids)
    service_registry.rag_service.ingest_queryset_reading_strategies(strategies)

@shared_task
def task_process_grobid_reading_strategies(strategy_ids):
    """Background task to execute Grobid section-aware reading strategies."""
    from .models import GrobidReadingStrategy
    strategies = GrobidReadingStrategy.objects.filter(id__in=strategy_ids)
    service_registry.rag_service.ingest_queryset_reading_strategies(strategies)


@shared_task
def sweep_unprocessed_documents():
    """
    Periodic task to find documents missing Grobid data or RAG chunks and queue them.
    """
    from django.db.models import Count
    from .models import Document
    from grobid_client.tasks import task_extract_grobid_metadata

    actions = []

    # 1. Find PDFs missing Grobid metadata
    missing_grobid = Document.objects.filter(
        file__icontains='.pdf',
        grobid_metadata__isnull=True
    )[:5]
    for doc in missing_grobid:
        task_extract_grobid_metadata.delay(doc.id)
    if missing_grobid:
        actions.append(f"Queued {missing_grobid.count()} PDFs for Grobid.")

    # 2. Find Docs missing standard RAG ingestion
    unindexed_docs = Document.objects.annotate(
            chunk_count=Count('readingstrategy__usages')
        ).exclude(
            file__icontains='.pdf',
            grobid_metadata__isnull=True
        ).filter(chunk_count=0)[:5]


    doc_ids = list(unindexed_docs.values_list('id', flat=True))
    if doc_ids:
        task_process_documents.delay(doc_ids)
        actions.append(f"Queued {len(doc_ids)} docs for RAG ingestion.")

    return " | ".join(actions) if actions else "No new documents to sweep."

@shared_task
def sweep_unprocessed_prompt_strategies():
    """
    Periodic task to ensure documents have semantic summaries indexed via PromptStrategy.
    """
    from .models import Document, PromptStrategy
    from django.db.models import Count
    actions = []
    
    # We want to find documents that are already chunked (have ReadingStrategies)
    # but do NOT have a PromptStrategy.
    docs_missing_prompt = Document.objects.annotate(
        chunk_count=Count('readingstrategy__usages')
    ).filter(
        chunk_count__gt=0
    ).exclude(
        promptstrategy__isnull=False
    )[:5]

    for doc in docs_missing_prompt:
        strat, created = PromptStrategy.objects.get_or_create(
            document=doc,
            strategy_description="Semantic Summary Index",
            defaults={"prompt": "Summarize the core concepts, methodologies, and findings in this text to serve as a semantic search index."}
        )
            
    if docs_missing_prompt:
        strat_ids = list(PromptStrategy.objects.filter(document__in=docs_missing_prompt).values_list('id', flat=True))
        task_process_reading_strategies.delay(strat_ids)
        actions.append(f"Queued {len(strat_ids)} PromptStrategies for Semantic Indexing.")
        
    return " | ".join(actions) if actions else "No new PromptStrategies to sweep."