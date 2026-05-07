from celery import shared_task
from background_resources.models import Document
from benchmarking.generators import generate_scenarios_for_document

@shared_task
def task_generate_benchmarks(document_ids, stride=5):
    """Background task to generate synthetic QA scenarios."""
    docs = Document.objects.filter(id__in=document_ids)
    for doc in docs:
        generate_scenarios_for_document(doc, stride=stride)