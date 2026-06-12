from benchmarking.models import ScenarioGroup, BenchmarkScenario
from background_resources.models import ReadingStrategy, RAGChunk
from llm_api.apps import service_registry
from pydantic import BaseModel, Field, ValidationError
from typing import List, Literal
import outlines


class SyntheticQA(BaseModel):
    question: str = Field(min_length=10, description="The question text")
    answer: str = Field(min_length=10, description="The ideal answer derived strictly from the text")
    keywords: List[str] = Field(min_length=1, description="Key terms that must appear in the retrieved context")
    type: Literal['Factoid', 'Reasoning'] = Field(description="The type of question")


class SyntheticQABatch(BaseModel):
    items: List[SyntheticQA] = Field(min_length=1, description="Must contain at least 1 scenario.")


def generate_scenarios_for_document(document, stride=5, group_name=None, log_callback=print):
    rag_service = service_registry.rag_service
    ai_service = service_registry.ai_service

    # Ensure we have chunks
    readings = ReadingStrategy.objects.filter(
        document=document,
        strategy_description="Default Chunking")
    if len(readings) > 1:
        strategy = readings.first()
        created = False
    else:
        strategy, created = ReadingStrategy.objects.get_or_create(
            document=document,
            strategy_description="Default Chunking"
        )
    if created or strategy.usages.count() == 0:
        log_callback("Reading document (Default Strategy)...")
        strategy.read_document(rag_service)

    chunk_ids = list(strategy.get_chunk_ids())
    total_chunks = len(chunk_ids)
    log_callback(f"Found {total_chunks} chunks. Processing every {stride}th chunk.")

    scenarios = []

    for i in range(0, total_chunks, stride):
        # Group up to 'stride' chunks together to give the LLM a richer context block
        batch_ids = chunk_ids[i:i+stride]
        chunks = rag_service.store.mget(batch_ids)
        valid_chunks = [c for c in chunks if c]
        if not valid_chunks:
            continue

        text = "\n\n".join([c.page_content for c in valid_chunks])
        if len(text) < 100:
            continue

        log_callback(f"Generating from chunk block {i + 1} to {min(i + stride, total_chunks)} of {total_chunks}...")

        prompt = f"""
        You are an expert examiner. Your task is to generate evaluation questions based on the provided technical text.

        TEXT:
        {text[:4000]}

        INSTRUCTIONS:
        Generate 2 questions that can be answered using ONLY the text above.
        1. One 'Factoid' question (retrieval of specific facts).
        2. One 'Reasoning' question (synthesis or explanation of concepts).

        For each question, provide:
        - The Question
        - The Ideal Answer (concise, derived from text)
        - Keywords (3-5 distinct words that are critical for retrieval)
        """
        
        messages = [{"role": "user", "content": prompt}]

        try:
            batch = ai_service.generate_outline(
                messages=messages,
                response_schema=SyntheticQABatch,
                max_new_tokens=1024,
                temperature=0.7
            )
            print("batch", batch)
            try:
                if isinstance(batch, dict):
                    batch = SyntheticQABatch.model_validate(batch)
                elif isinstance(batch, str):
                    batch = SyntheticQABatch.model_validate_json(batch)
                elif isinstance(batch, list) and len(batch) > 0:
                    batch = batch[0]
            except Exception as e:
                batch = None
                print("Error on response validation:", e)
            if batch:
                # Fetch the actual Django DB object to link as a ForeignKey

                primary_chunk_id = chunk_ids[i]
                rag_chunk = RAGChunk.objects.filter(chunk_id=primary_chunk_id).first()
                for item in batch.items:
                    scenarios.append(BenchmarkScenario(
                        question=item.question,
                        ideal_answer=item.answer,
                        expected_keywords=item.keywords,
                        source_doc=document,
                        source_chunk=rag_chunk
                    ))

        except Exception as e:
            import traceback
            log_callback(f"Failed to generate for chunk {i}: {e}\n{traceback.format_exc()}")

    if scenarios:
        name = group_name or f"Synthetic - {document.title}"
        group, _ = ScenarioGroup.objects.get_or_create(name=name)

        saved_scenarios = BenchmarkScenario.objects.bulk_create(scenarios)
        group.scenarios.add(*saved_scenarios)
        log_callback(f"Created {len(saved_scenarios)} scenarios in group '{name}'")
        return len(saved_scenarios)

    return 0