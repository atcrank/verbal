import os
import sys
import json
import logging
from pathlib import Path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

# Set web role to proxy generation requests to the running inference server on port 8001
os.environ["VERBAL_ROLE"] = "web"
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "verbal_config.settings")

import django
django.setup()

import requests
from django.core.files import File
from django.core import serializers
from background_resources.models import Document, RAGChunk, GrobidReadingStrategy, ReadingStrategy, StrategyChunkUsage
from grobid_client.models import Reference, Citation
from grobid_client.tasks import task_extract_grobid_metadata
from llm_api.apps import service_registry

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FODDER_DIR = Path(__file__).resolve().parent / "doctest_fodder"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

def check_grobid_health():
    from django.conf import settings
    grobid_url = getattr(settings, "GROBID_URL", "http://localhost:8070")
    try:
        r = requests.get(f"{grobid_url.rstrip('/')}/api/isalive", timeout=5)
        if r.status_code == 200:
            logger.info(f"Grobid container is healthy at {grobid_url}")
            return True
        logger.error(f"Grobid returned status {r.status_code}")
        return False
    except Exception as e:
        logger.error(f"Could not connect to Grobid at {grobid_url}: {e}")
        return False

def process_firefighting_papers():
    if not check_grobid_health():
        logger.error("Grobid is not available. Please ensure the Grobid container is running.")
        sys.exit(1)

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
    rag_service = service_registry.rag_service

    pdf_files = [f for f in FODDER_DIR.iterdir() if f.suffix.lower() == ".pdf"]
    logger.info(f"Found {len(pdf_files)} PDF papers in {FODDER_DIR}")

    processed_docs = []
    
    for pdf_path in pdf_files:
        logger.info(f"\n==========================================")
        logger.info(f"Processing paper: {pdf_path.name}")
        logger.info(f"==========================================")
        
        # Clean title from filename
        clean_title = pdf_path.stem.replace("_", " ").replace("  ", " ").strip()
        if "Talavera" in clean_title:
            clean_title = "An autonomous ground robot to support firefighters' interventions in indoor emergencies"
        elif "Penders" in clean_title:
            clean_title = "A robot swarm assisting a human fire-fighter"
        elif "fire-06-00093" in clean_title:
            clean_title = "An Indoor Autonomous Inspection and Firefighting Robot Based on SLAM and Flame Image Recognition"

        doc = Document.objects.filter(title=clean_title).first()
        if not doc:
            doc = Document(
                title=clean_title,
                metadata={"category": "FIRE_ROBOTICS"}
            )
            with open(pdf_path, "rb") as f:
                doc.file.save(pdf_path.name, File(f), save=True)
        elif not doc.file:
            with open(pdf_path, "rb") as f:
                doc.file.save(pdf_path.name, File(f), save=True)
        
        logger.info(f"Saved Document ID {doc.id}: '{doc.title}'")

        # Step 1: Run Grobid extraction
        logger.info(f"Invoking Grobid metadata extraction task for Document {doc.id}...")
        task_result = task_extract_grobid_metadata.func(doc.id)
        logger.info(f"Grobid extraction result: {task_result}")

        # Step 2: Create and apply GrobidReadingStrategy for semantic chunks
        grobid_strat, _ = GrobidReadingStrategy.objects.get_or_create(document=doc)
        if grobid_strat.usages.count() == 0:
            logger.info("Applying GrobidReadingStrategy to generate section-aware chunks...")
            grobid_strat.read_document(rag_service_inject=rag_service)
        else:
            logger.info(f"GrobidReadingStrategy already has {grobid_strat.usages.count()} chunks indexed.")
        
        chunk_count = grobid_strat.usages.count()
        logger.info(f"Generated {chunk_count} semantic chunks for '{doc.title}'")

        processed_docs.append(doc)

    # Step 3: Serialize all extracted records into a reproducible fixture
    logger.info("\nExporting extracted objects to fixture...")
    all_objects = []
    
    all_docs = Document.objects.filter(id__in=[d.id for d in processed_docs])
    all_objects.extend(list(all_docs))
    
    all_refs = Reference.objects.filter(document__in=all_docs)
    all_objects.extend(list(all_refs))
    
    all_cits = Citation.objects.filter(source_reference__in=all_refs)
    all_objects.extend(list(all_cits))
    
    all_strats = GrobidReadingStrategy.objects.filter(document__in=all_docs)
    all_objects.extend(list(all_strats))
    
    all_usages = StrategyChunkUsage.objects.filter(
        content_type__model="grobidreadingstrategy",
        object_id__in=[s.id for s in all_strats]
    )
    all_objects.extend(list(all_usages))
    
    chunk_pks = all_usages.values_list("chunk_id", flat=True)
    all_chunks = RAGChunk.objects.filter(id__in=chunk_pks)
    all_objects.extend(list(all_chunks))

    fixture_path = FIXTURES_DIR / "firefighting_chunks.json"
    json_data = serializers.serialize("json", all_objects, indent=2)
    
    with open(fixture_path, "w") as f:
        f.write(json_data)
        
    logger.info(f"Successfully exported {len(all_objects)} objects to {fixture_path}")
    logger.info(f"- Documents: {len(all_docs)}")
    logger.info(f"- References: {len(all_refs)}")
    logger.info(f"- Citations: {len(all_cits)}")
    logger.info(f"- Strategies: {len(all_strats)}")
    logger.info(f"- Semantic Chunks: {len(all_chunks)}")

if __name__ == "__main__":
    process_firefighting_papers()
