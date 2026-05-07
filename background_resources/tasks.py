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