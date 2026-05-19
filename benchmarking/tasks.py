from celery import shared_task
from background_resources.models import Document
from benchmarking.generators import generate_scenarios_for_document

@shared_task
def task_generate_benchmarks(document_ids, stride=5):
    """Background task to generate synthetic QA scenarios."""
    docs = Document.objects.filter(id__in=document_ids)
    for doc in docs:
        generate_scenarios_for_document(doc, stride=stride)


@shared_task
def sweep_benchmark_generation():
    """
    Periodic task to generate synthetic scenarios for recently uploaded documents.
    """
    from background_resources.models import Document

    # Heuristic: Process the 3 most recently uploaded documents
    recent_docs = Document.objects.order_by('-uploaded_at')[:3]
    doc_ids = list(recent_docs.values_list('id', flat=True))
    if doc_ids:
        task_generate_benchmarks.delay(doc_ids)
    return f"Queued benchmark generation for recent documents: {doc_ids}"