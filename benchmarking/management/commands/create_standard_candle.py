from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from background_resources.models import Document, ReadingStrategy
from benchmarking.models import BenchmarkCorpus, BenchmarkScenario, ScenarioGroup, Investigation, Experiment
from llm_api.apps import service_registry


class Command(BaseCommand):
    help = "Creates a 'Standard Candle' dataset (Documents + Scenarios) for verifying the RAG engine."

    def handle(self, *args, **options):
        self.stdout.write("🕯️  Lighting the Standard Candle...")

        # 1. Define The Data
        # We use hardcoded text to ensure the benchmark is 100% reproducible across environments.

        docs_data = [
            {
                "title": "Standard Candle - Paris",
                "filename": "standard_candle_paris.txt",
                "content": """Paris is the capital and most populous city of France. With an official estimated population of 2,102,650 residents as of 1 January 2023 in an area of more than 105 km2 (41 sq mi), Paris is the fourth-most populated city in the European Union and the 30th most densely populated city in the world in 2020. Since the 17th century, Paris has been one of the world's major centres of finance, diplomacy, commerce, culture, fashion, gastronomy and many areas. For its leading role in the arts and sciences, as well as its very early and extensive system of street lighting, in the 19th century, it became known as the City of Light.
                The Eiffel Tower was constructed for the 1889 World's Fair. It is named after the engineer Gustave Eiffel, whose company designed and built the tower.
                The Louvre Museum is the world's most-visited art museum, with a collection that spans the work of ancient civilizations to the mid-19th century.""",
                "scenarios": [
                    {
                        "question": "What is the capital of France?",
                        "answer": "Paris",
                        "keywords": ["Paris", "capital", "France"]
                    },
                    {
                        "question": "Why is Paris called the City of Light?",
                        "answer": "Because of its leading role in the arts and sciences, and its early and extensive system of street lighting.",
                        "keywords": ["City of Light", "arts", "sciences", "street lighting"]
                    },
                    {
                        "question": "Who designed the Eiffel Tower?",
                        "answer": "Gustave Eiffel's company.",
                        "keywords": ["Gustave Eiffel", "company", "designed"]
                    }
                ]
            },
            {
                "title": "Standard Candle - Apollo 11",
                "filename": "standard_candle_apollo11.txt",
                "content": """Apollo 11 was the American spaceflight that first landed humans on the Moon. Commander Neil Armstrong and lunar module pilot Buzz Aldrin landed the Apollo Lunar Module Eagle on July 20, 1969, at 20:17 UTC, and Armstrong became the first person to step onto the lunar surface six hours and 39 minutes later, on July 21 at 02:56 UTC. Aldrin joined him 19 minutes later, and they spent about two and a quarter hours together exploring the site they had named Tranquility Base upon landing. Armstrong and Aldrin collected 47.5 pounds (21.55 kg) of lunar material to bring back to Earth as pilot Michael Collins flew the Command Module Columbia in lunar orbit, and were on the Moon's surface for 21 hours, 36 minutes. Armstrong's first step onto the lunar surface was broadcast on live TV to a worldwide audience. He described the event as "one small step for man, one giant leap for mankind".""",
                "scenarios": [
                    {
                        "question": "Who was the first person to step on the moon?",
                        "answer": "Neil Armstrong",
                        "keywords": ["Neil Armstrong", "first person", "step"]
                    },
                    {
                        "question": "What was the name of the lunar module?",
                        "answer": "Eagle",
                        "keywords": ["Eagle", "Lunar Module"]
                    },
                    {
                        "question": "How much lunar material did they collect?",
                        "answer": "47.5 pounds (21.55 kg)",
                        "keywords": ["47.5 pounds", "21.55 kg", "collected"]
                    }
                ]
            }
        ]

        # 2. Setup Corpus and Group
        corpus_name = "Standard Candle Corpus"
        group_name = "Standard Candle Validation Set"
        investigation_name = "Standard Candle Investigation"
        experiment_name = "Baseline Run"

        # Cleanup existing to allow re-running
        # Deleting the investigation will cascade delete the experiments
        Investigation.objects.filter(name=investigation_name).delete()
        BenchmarkCorpus.objects.filter(name=corpus_name).delete()
        
        # Clean up scenarios and group
        existing_group = ScenarioGroup.objects.filter(name=group_name).first()
        if existing_group:
            self.stdout.write(f"Cleaning up existing scenario group: {group_name}")
            existing_group.scenarios.all().delete()
            existing_group.delete()

        # Note: We don't delete the Documents themselves automatically to be safe,
        # but we check if they exist.

        corpus = BenchmarkCorpus.objects.create(name=corpus_name,
                                                description="A small, verified dataset for regression testing.")
        group = ScenarioGroup.objects.create(name=group_name, description="Questions for the Standard Candle corpus.")
        
        investigation = Investigation.objects.create(
            name=investigation_name,
            description="Automated validation of the RAG engine using the Standard Candle dataset."
        )

        experiment = Experiment.objects.create(
            name=experiment_name,
            description="Initial baseline run using default settings.",
            investigation=investigation,
            corpus=corpus,
            scenario_group=group,
            iterations=1
        )

        rag_service = service_registry.rag_service

        for entry in docs_data:
            # A. Create/Get Document
            doc_title = entry['title']

            # Check if exists to avoid duplicates
            doc = Document.objects.filter(title=doc_title).first()
            if not doc:
                self.stdout.write(f"Creating document: {doc_title}")
                doc = Document(title=doc_title)
                # Save content to file field
                doc.file.save(entry['filename'], ContentFile(entry['content']))
                doc.save()
            else:
                self.stdout.write(f"Document {doc_title} already exists. Using existing.")

            # B. Ensure Ingestion (Default Strategy)
            strategy, created = ReadingStrategy.objects.get_or_create(
                document=doc,
                strategy_description="Default Chunking"
            )

            # Force re-read if it's new or empty, to ensure vector store is populated
            if created or strategy.usages.count() == 0:
                self.stdout.write(f"  - Ingesting {doc_title}...")
                strategy.read_document(rag_service)

            # C. Add to Corpus
            corpus.documents.add(doc)

            # D. Create Scenarios
            for s_data in entry['scenarios']:
                scenario = BenchmarkScenario.objects.create(
                    question=s_data['question'],
                    ideal_answer=s_data['answer'],
                    expected_keywords=s_data['keywords']
                )
                group.scenarios.add(scenario)
                
        # VERY IMPORTANT: Save the in-memory FAISS index to disk!
        rag_service.save_db()

        self.stdout.write(self.style.SUCCESS(f"✅ Standard Candle setup complete."))
        self.stdout.write(f"Corpus: '{corpus.name}'")
        self.stdout.write(f"Scenario Group: '{group.name}'")
        self.stdout.write(f"Investigation: '{investigation.name}'")
        self.stdout.write(f"Experiment: '{experiment.name}'")
        self.stdout.write("\nYou can now run a benchmark using:")
        self.stdout.write(f"python manage.py run_benchmark '{corpus.name}' '{experiment.name}'")
