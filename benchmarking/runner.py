import time
import os
import re
import shutil
import tempfile
import numpy as np
from django.db import transaction
from pydantic import BaseModel, Field, field_validator
from llm_api.apps import service_registry
from benchmarking.models import BenchmarkRun, BenchmarkResult, BenchmarkScenario
from background_resources.rag_service import RAGService

class EvaluationScore(BaseModel):
    reasoning: str = Field(description="Brief justification for the score")
    score: int = Field(ge=1, le=5, description="Integer score from 1 (Poor) to 5 (Excellent)")

    @field_validator('score', mode='before')
    @classmethod
    def clamp_score(cls, v):
        # Robustness: Handle models that ignore the 1-5 scale and give 10
        try:
            val = int(v)
            if val > 5: return 5
            if val < 1: return 1
            return val
        except (ValueError, TypeError):
            return 1

def evaluate_metric(ai_service, prompt_template, num_sequences=5, **kwargs):
    """
    Generic LLM-as-a-Judge evaluator using parallel sampling.
    Generates multiple sequences, discards parsing failures, and averages the valid scores.
    Returns (average_normalized_score, success_rate).
    If all parsing fails, returns (-1.0, 0.0) as a sentinel.
    """
    prompt = prompt_template.format(**kwargs)
    messages = [{"role": "user", "content": prompt}]
    
    try:
        responses = ai_service.generate_outline(
            messages=messages,
            response_schema=EvaluationScore,
            max_new_tokens=500,
            num_return_sequences=num_sequences
        )
        
        if not isinstance(responses, list):
            responses = [responses]
            
        valid_scores = []
        for resp in responses:
            try:
                # Handle both Pydantic objects (normal) and JSON strings (fallback/legacy)
                if isinstance(resp, str):
                    evaluation = EvaluationScore.model_validate_json(resp)
                elif isinstance(resp, dict):
                    evaluation = EvaluationScore.model_validate(resp)
                else:
                    evaluation = resp
                print(f"DEBUG EVAL | Score: {evaluation.score} | Reasoning: {evaluation.reasoning}")
                valid_scores.append((evaluation.score - 1) / 4.0)
            except Exception:
                continue
                
        success_rate = len(valid_scores) / len(responses) if responses else 0.0
        avg_score = sum(valid_scores) / len(valid_scores) if valid_scores else -1.0
        return avg_score, success_rate
    except Exception as e:
        print(f"Evaluation completely failed: {e}. Prompt preview: {prompt[:100]}...")
        return -1.0, 0.0


def _switch_active_model(experiment, log_callback):
    """Switches the globally active AI model if the experiment demands it."""
    if not experiment.selected_model:
        return
        
    current_model_id = getattr(service_registry.ai_service, 'model_id', None)
    target_model_id = experiment.selected_model.hf_model_id
    
    if current_model_id != target_model_id:
        log_callback(f"⚠️ Switching AI Model to {experiment.selected_model.name}...")
        from llm_api.models import LocalAIModel
        LocalAIModel.objects.update(is_system_active=False)
        experiment.selected_model.is_system_active = True
        experiment.selected_model.save()
        service_registry.reload_ai_service()
        log_callback(f"✅ Model switched to {target_model_id}")


def _ingest_test_corpus(corpus, rag_service, rag_strategy, chunk_size_override, chunk_overlap_override, log_callback):
    """Handles the temporary RAG ingestion isolated by strategy."""
    if rag_strategy == 'none':
        log_callback("Skipping RAG ingestion because rag_strategy is set to 'none'.")
        return

    log_callback(f"Ingesting {corpus.documents.count()} documents from corpus '{corpus.name}'...")
    for doc in corpus.documents.all():
        strategies = []

        if rag_strategy in ['all', 'default', 'basic']:
            strategies += list(doc.readingstrategy_set.all())
        if rag_strategy in ['all', 'grobid', 'semantic']:
            strategies += list(doc.grobidreadingstrategy_set.all())
        if rag_strategy in ['all', 'prompt']:
            strategies += list(doc.promptstrategy_set.all())
        if rag_strategy in ['all', 'regex']:
            strategies += list(doc.regexstrategy_set.all())
        if rag_strategy in ['all', 'abbreviations']:
            strategies += list(doc.abbreviationsreadingstrategy_set.all())

        if not strategies:
            if chunk_size_override:
                log_callback(f"  - Default Ingestion (Override size: {chunk_size_override})")
            rag_service.convert_chunk_store_document(
                doc, chunk_size=chunk_size_override, chunk_overlap=chunk_overlap_override
            )
        else:
            base_chunks, _ = rag_service.convert_chunk_store_document(
                doc, chunk_size=chunk_size_override, chunk_overlap=chunk_overlap_override
            )

            for strategy in strategies:
                if chunk_size_override:
                    strategy.chunk_size_override = chunk_size_override
                if chunk_overlap_override:
                    strategy.chunk_overlap_override = chunk_overlap_override

                with transaction.atomic():
                    strategy.apply_strategy(rag_service, force=True, source_chunks=base_chunks)
                    transaction.set_rollback(True)

    rag_service.save_db()


def _clean_synthetic_question(question: str) -> str:
    """Strips RAG-killing fluff from synthetically generated questions."""
    return re.sub(
        r'^(according to the (text|document|passage|article|excerpt)|based on the (text|document|passage|article|excerpt))[,:]?\s*',
        '', question, flags=re.IGNORECASE
    )


def _calculate_semantic_similarity(embeddings, generated_text: str, ideal_answer: str) -> float:
    """Calculates cosine similarity between two text strings using the RAG embedding model."""
    if not ideal_answer:
        return 0.0
    vec_gen = embeddings.embed_query(generated_text)
    vec_ideal = embeddings.embed_query(ideal_answer)
    dot = np.dot(vec_gen, vec_ideal)
    norm_g = np.linalg.norm(vec_gen)
    norm_i = np.linalg.norm(vec_ideal)
    if norm_g > 0 and norm_i > 0:
        return float(dot / (norm_g * norm_i))
    return 0.0


def _evaluate_llm_metrics(ai_service, rag_strategy, rag_text_block, question, cleaned_response):
    """Runs LLM-as-a-judge for Faithfulness and Relevance metrics."""
    faith_score = -1.0
    faith_success = 0.0
    
    if rag_strategy != 'none':
        faith_prompt = """
        You are a strict judge. Evaluate if the ANSWER is derived ONLY from the CONTEXT. 
        Score 1 to 5. 
        Score 5 for an answer supported entirely by the context, down to 1 if the model has answered without reference to the context. MAXIMUM SCORE IS 5.
        CONTEXT: {context}
        ANSWER: {answer}
        """
        faith_score, faith_success = evaluate_metric(
            ai_service, faith_prompt, 
            context=rag_text_block[:2000], answer=cleaned_response
        )

    rel_prompt = """
    You are a strict judge. Evaluate if the ANSWER directly addresses the QUESTION.
    Score 1 to 5.
    Score 5 for an answer very specific to the question, down to 1 if the model has not answered the question correctly. MAXIMUM SCORE IS 5.
    QUESTION: {question}
    ANSWER: {answer}
    """
    rel_score, rel_success = evaluate_metric(
        ai_service, rel_prompt, 
        question=question, answer=cleaned_response
    )
    
    return faith_score, faith_success, rel_score, rel_success


def _generate_candidate_responses(ai_service, experiment, scenario, rag_text_block):
    """Routes the generation step to the requested target (Direct, Blueprint, Grips)."""
    config = experiment.configuration or {}
    generation_target = config.get('generation_target', 'direct').lower()
    iterations = experiment.iterations
    
    raw_responses = []

    if generation_target == 'blueprint':
        blueprint_id = config.get('blueprint_id')
        if not blueprint_id:
            return [f"Error: 'blueprint_id' missing in configuration."] * iterations
        
        from metacognition.tasks import run_blueprint
        for _ in range(iterations):
            # The Blueprint internally calls service_registry.rag_service.get_context().
            # Because we patched service_registry in run_benchmark_suite, it will
            # seamlessly query our temporary, strategy-isolated FAISS index!
            result = run_blueprint(blueprint_id, scenario.question)
            if "error" not in result:
                raw_responses.append(result.get("final_response", ""))
            else:
                raw_responses.append(f"Blueprint Error: {result['error']}")

    elif generation_target == 'grips':
        # TODO: Integrate Grips context generation here
        for _ in range(iterations):
            raw_responses.append("Grips augmented generation not yet implemented.")

    else:  # 'direct'
        system_prompt = "You are a helpful assistant."
        user_content = scenario.question
        rag_strategy = config.get('rag_strategy', 'none').lower()
        
        if rag_strategy != 'none' and rag_text_block:
            user_content += "\n\nRelevant Context:\n" + rag_text_block

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        raw_responses = ai_service.generate_response(
            messages=messages,
            max_new_tokens=300,
            num_return_sequences=iterations
        )
        
    return raw_responses


def run_benchmark_suite(experiment, corpus, log_callback=None):
    """
    Executes a benchmark run for a given Experiment and Corpus.
    """
    if log_callback is None:
        def log_callback(msg): pass

    # 0. Context Switching (Model)
    _switch_active_model(experiment, log_callback)

    # 1. Setup Temporary RAG Environment
    temp_dir = tempfile.mkdtemp()
    temp_vector_store = os.path.join(temp_dir, 'vector_store')
    temp_chunk_store = os.path.join(temp_dir, 'chunk_store')
    os.makedirs(temp_vector_store, exist_ok=True)
    os.makedirs(temp_chunk_store, exist_ok=True)

    log_callback(f"Initializing Temporary RAG Service in {temp_dir}...")
    
    # Initialize fresh RAG service with temp paths
    # Note: This re-loads the embedding model which adds some overhead but ensures isolation
    rag_service = RAGService(vector_store_path=temp_vector_store, chunk_store_path=temp_chunk_store)
    rag_service.load_models() # Initialize generators for PromptStrategy etc.
    ai_service = service_registry.ai_service
    
    # CRITICAL: Temporarily override the global RAG service so any Blueprints executed
    # during this benchmark run hit the isolated, strategy-specific test corpus!
    original_rag_service = service_registry._rag_service
    service_registry._rag_service = rag_service

    # Capture Configuration Snapshot
    config_snapshot = experiment.configuration or {}
    rag_strategy = config_snapshot.get('rag_strategy', config_snapshot.get('target_strategy', 'all')).lower()
    chunk_size_override = config_snapshot.get('chunk_size')
    chunk_overlap_override = config_snapshot.get('chunk_overlap')

    # RAG Service Config
    if hasattr(rag_service, 'embeddings') and hasattr(rag_service.embeddings, 'model_name'):
        config_snapshot['embedding_model'] = rag_service.embeddings.model_name or "Unknown"

    # AI Service Config (Introspection)
    config_snapshot['ai_model_id'] = getattr(ai_service, 'model_id', 'unknown')
    config_snapshot['chain_of_thought'] = getattr(ai_service, 'chain_of_thought', False)

    # Update Experiment with the latest config
    experiment.configuration = config_snapshot
    experiment.save()

    # Determine scenarios: Prefer Experiment's Group, fallback to Corpus (legacy/migration support)
    if experiment.scenario_group:
        scenarios = experiment.scenario_group.scenarios.all()
    else:
        # Fallback if no group assigned (or legacy data)
        if hasattr(corpus, 'benchmarkscenario_set'):
            scenarios = corpus.benchmarkscenario_set.all()
        else:
            scenarios = BenchmarkScenario.objects.none()

    if not scenarios.exists():
        log_callback(f"No scenarios found for experiment '{experiment.name}'. Aborting.")
        return None
    
    # 2. Ingest Corpus Documents into Temp Store 
    _ingest_test_corpus(corpus, rag_service, rag_strategy, chunk_size_override, chunk_overlap_override, log_callback)

    try:
        # Create Run Record
        run_record = BenchmarkRun.objects.create(
            experiment=experiment,
            corpus=corpus,
            configuration_snapshot=config_snapshot
        )

        log_callback(f"Starting Run #{run_record.id} with {scenarios.count()} scenarios.")

        total_rag = 0.0
        total_sem = 0.0
        
        total_faith = 0.0
        valid_faith_count = 0
        total_rel = 0.0
        valid_rel_count = 0
        total_eval_success = 0.0
        eval_attempts = 0

        for scenario in scenarios:
            log_callback(f"  > {scenario.question[:60]}...")

            # --- A. Execution ---
            start_time = time.perf_counter()

            # 1. Retrieval (Happens once per scenario)
            # Metrics: Which strategy found these docs?
            strategy_hits = {}
            clean_question = _clean_synthetic_question(scenario.question)

            if rag_strategy != 'none':
                rag_docs = rag_service.get_context(clean_question)
                rag_text_block = "\n\n".join([d.page_content for d in rag_docs])

                # Metrics: Which strategy found these docs?
                for d in rag_docs:
                    stype = d.metadata.get('strat_type', 'Raw/Base')
                    strategy_hits[stype] = strategy_hits.get(stype, 0) + 1
            else:
                rag_docs = []
                rag_text_block = ""

            # 2. Generation
            raw_responses = _generate_candidate_responses(ai_service, experiment, scenario, rag_text_block)

            # --- B. Scoring (RAG) ---
            # RAG Recall is calculated once per scenario since retrieval is constant
            hits = [k for k in scenario.expected_keywords if k.lower() in rag_text_block.lower()]
            rag_score = len(hits) / len(scenario.expected_keywords) if scenario.expected_keywords else 1.0
            total_rag += rag_score

            for raw_response in raw_responses:
                generation_target = config_snapshot.get('generation_target', 'direct').lower()
                if generation_target == 'direct':
                    cleaned_response = ai_service.clean_response(raw_response)
                else:
                    # Blueprints output formatted strings; cleaning them strips necessary structure
                    cleaned_response = raw_response
                    
                duration = time.perf_counter() - start_time

                # --- C. Scoring (Semantic) ---
                sem_score = _calculate_semantic_similarity(rag_service.embeddings, cleaned_response, scenario.ideal_answer)

                # --- D. Scoring (LLM-as-a-Judge) ---
                faith_score, faith_success, rel_score, rel_success = _evaluate_llm_metrics(
                    ai_service, rag_strategy, rag_text_block, scenario.question, cleaned_response
                )
                
                if faith_score != -1.0:
                    total_faith += faith_score
                    valid_faith_count += 1
                if rel_score != -1.0:
                    total_rel += rel_score
                    valid_rel_count += 1
                    
                total_eval_success += (faith_success + rel_success) / 2.0
                eval_attempts += 1


                # --- D. Save Result ---
                BenchmarkResult.objects.create(
                    run=run_record,
                    scenario=scenario,
                    prompt_text=clean_question,
                    raw_retrieved_text=rag_text_block,
                    generated_response=cleaned_response,
                    duration_seconds=duration,
                    rag_recall_score=rag_score,
                    semantic_score=sem_score,
                    faithfulness_score=faith_score if faith_score != -1.0 else None,
                    relevance_score=rel_score if rel_score != -1.0 else None,
                    extra_metrics={"strategy_hits": strategy_hits, "eval_success_rate": (faith_success + rel_success) / 2.0}
                )

                total_sem += sem_score

        # 4. Finalize Run
        eval_count = scenarios.count() * experiment.iterations

        if eval_count > 0:
            run_record.average_rag_score = total_rag / scenarios.count()
            run_record.average_semantic_score = total_sem / eval_count
            run_record.average_faithfulness = total_faith / valid_faith_count if valid_faith_count > 0 else None
            run_record.average_relevance = total_rel / valid_rel_count if valid_rel_count > 0 else None
            run_record.eval_success_rate = total_eval_success / eval_attempts if eval_attempts > 0 else 0.0
        else:
            run_record.average_rag_score = 0.0
            run_record.average_semantic_score = 0.0
            run_record.average_faithfulness = None
            run_record.average_relevance = None
            run_record.eval_success_rate = 0.0

        run_record.save()

        faith_str = f"{run_record.average_faithfulness:.2f}" if run_record.average_faithfulness is not None else "N/A"
        log_callback(
            f"Run Complete. RAG: {run_record.average_rag_score:.2f} | Sem: {run_record.average_semantic_score:.2f} | Faith: {faith_str}")
        return run_record

    finally:
        # Restore the global RAG service
        service_registry._rag_service = original_rag_service
        # Cleanup Temp Directory
        shutil.rmtree(temp_dir)