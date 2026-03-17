from django.core.management.base import BaseCommand
from background_resources.models import Document
from benchmarking.generators import generate_scenarios_for_document


class Command(BaseCommand):
    help = 'Compile a set of synthetic scenarios to use in benchmarking.'

    def add_arguments(self, parser):
        parser.add_argument('document_id', type=int, help='ID of the Document to process')
        parser.add_argument('--stride', type=int, default=5, help='Process every Nth chunk')
        parser.add_argument('--group-name', type=str, default=None, help='Name for the ScenarioGroup')

    def handle(self, *args, **options):
        doc_id = options['document_id']
        try:
            doc = Document.objects.get(pk=doc_id)
        except Document.DoesNotExist:
            self.stdout.write(self.style.ERROR(f"Document {doc_id} not found"))
            return

        generate_scenarios_for_document(
            doc, 
            stride=options['stride'], 
            group_name=options['group_name'],
            log_callback=self.stdout.write
        )