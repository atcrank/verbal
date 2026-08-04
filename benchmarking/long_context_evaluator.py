import os
import time
import tempfile
from uuid import uuid4
from django.utils import timezone
from benchmarking.models import BenchmarkRun, BenchmarkScenario
from benchmarking.runner import (
    _switch_active_model, _ingest_test_corpus, _calculate_semantic_similarity,
    _evaluate_llm_metrics
)
from background_resources.rag_service import RAGService
from llm_api.apps import service_registry
from llm_api.models import Conversation

def run_long_context_evaluation(experiment, corpus, log_callback=None):
    """
    Runs scenarios sequentially within a single conversation,
    tracking cumulative_input_tokens per result.
    
    Unlike run_benchmark_suite() which treats each scenario independently,
    this runner accumulates conversation history across scenarios to measure
    how performance degrades as context grows.
    """
    if log_callback is None:
        def log_callback(msg): pass

    _switch_active_model(experiment, log_callback)

    temp_dir = tempfile.mkdtemp()
    temp_vector_store = os.path.join(temp_dir, 'vector_store')
    temp_chunk_store = os.path.join(temp_dir, 'chunk_store')
    os.makedirs(temp_vector_store, exist_ok=True)
    os.makedirs(temp_chunk_store, exist_ok=True)
    temp_collection_name = f"verbal_lceval_{uuid4().hex}"

    log_callback(f"Initializing Temporary RAG Service for Long-Context Eval in {temp_dir}...")
    rag_service = RAGService(collection_name=temp_collection_name)
    rag_service.load_models()
    ai_service = service_registry.ai_service
    
    original_rag_service = service_registry._rag_service
    service_registry._rag_service = rag_service

    config_snapshot = experiment.configuration or {}
    rag_strategy = config_snapshot.get('rag_strategy', config_snapshot.get('target_strategy', 'none')).lower()
    chunk_size_override = config_snapshot.get('chunk_size')
    chunk_overlap_override = config_snapshot.get('chunk_overlap')
    context_mode = config_snapshot.get('context_mode', 'chat').lower()
    
    if hasattr(rag_service, 'embeddings') and hasattr(rag_service.embeddings, 'model_name'):
        config_snapshot['embedding_model'] = rag_service.embeddings.model_name or "Unknown"
    config_snapshot['ai_model_id'] = getattr(ai_service, 'model_id', 'unknown')
    
    experiment.configuration = config_snapshot
    experiment.save()

    if experiment.scenario_group:
        scenarios = experiment.scenario_group.scenarios.all()
    else:
        if hasattr(corpus, 'benchmarkscenario_set'):
            scenarios = corpus.benchmarkscenario_set.all()
        else:
            scenarios = BenchmarkScenario.objects.none()

    if not scenarios.exists():
        log_callback(f"No scenarios found for experiment '{experiment.name}'.")
        return None

    _ingest_test_corpus(corpus, rag_service, rag_strategy, chunk_size_override, chunk_overlap_override, log_callback)

    try:
        from benchmarking.models import BenchmarkResult
        
        run_record = BenchmarkRun.objects.create(
            experiment=experiment,
            corpus=corpus,
            configuration_snapshot=config_snapshot
        )

        # Create the continuous conversation
        from django.contrib.auth.models import User
        # Find any valid user (or create a dummy one for testing)
        user = User.objects.first()
        if not user:
            user = User.objects.create(username="lceval_runner")
            
        conversation = Conversation.objects.create(
            title=f"LC Eval {experiment.name} {timezone.now().date()}",
            user=user
        )

        messages = [
            {"role": "system", "content": "You are a helpful assistant."}
        ]
        
        total_eval_success = 0.0
        eval_attempts = 0
        cumulative_tokens = 0

        for scenario in scenarios:
            log_callback(f"  > {scenario.question[:60]}...")
            start_time = time.perf_counter()

            # Retrieval
            rag_text_block = ""
            if rag_strategy != 'none':
                clean_question = scenario.question.strip()
                rag_docs = rag_service.get_context(clean_question)
                rag_text_block = "\n\n".join([d.page_content for d in rag_docs])
            
            hits = [k for k in scenario.expected_keywords if k.lower() in rag_text_block.lower()]
            rag_score = len(hits) / len(scenario.expected_keywords) if scenario.expected_keywords else 1.0

            traj_metrics = {}
            cleaned_response = ""
            
            if context_mode == 'blueprint':
                blueprint_id = config_snapshot.get('blueprint_id')
                if not blueprint_id:
                    raise ValueError("'blueprint_id' missing in configuration for blueprint context_mode")
                    
                from metacognition.tasks import run_blueprint
                result = run_blueprint(blueprint_id, scenario.question, conversation_id=conversation.id)
                
                if "error" not in result:
                    cleaned_response = result.get("final_response", "")
                    if "internal_monologue" in result:
                        traj_metrics["internal_monologue"] = result["internal_monologue"]
                else:
                    cleaned_response = f"Blueprint Error: {result['error']}"
                    
                # Token count estimate (LangGraph doesn't easily expose tokens without checking prompt logs)
                # We'll use our proxy method
                cumulative_tokens = ai_service.count_conversation_tokens(messages)
                traj_metrics["cumulative_input_tokens"] = cumulative_tokens
                
            else: # chat
                user_content = scenario.question
                if rag_strategy != 'none' and rag_text_block:
                    user_content += "\n\nRelevant Context:\n" + rag_text_block
                    
                messages.append({"role": "user", "content": user_content})
                
                responses_strs = ai_service.generate_response2(
                    messages=messages,
                    max_new_tokens=300,
                    num_return_sequences=1
                )
                raw_response = responses_strs[0]
                cleaned_response = ai_service.clean_response(raw_response)
                messages.append({"role": "assistant", "content": cleaned_response})
                
                from llm_api.ai_service import get_last_generation_metrics
                metrics = get_last_generation_metrics()
                if metrics:
                    traj_metrics["tokens_per_second"] = metrics.tokens_per_second
                    traj_metrics["generation_duration_ms"] = metrics.total_duration_ms
                    
                # Extract cumulative input tokens for this turn
                cumulative_tokens = ai_service.count_conversation_tokens(messages[:-1])
                traj_metrics["cumulative_input_tokens"] = cumulative_tokens

            duration = time.perf_counter() - start_time
            sem_score = _calculate_semantic_similarity(rag_service.embeddings, cleaned_response, scenario.ideal_answer)

            faith_score, faith_success, rel_score, rel_success = _evaluate_llm_metrics(
                ai_service, rag_strategy, rag_text_block, scenario.question, cleaned_response
            )
            
            if faith_score != -1.0 or rel_score != -1.0:
                total_eval_success += (faith_success + rel_success) / 2.0
                eval_attempts += 1
                
            BenchmarkResult.objects.create(
                run=run_record,
                scenario=scenario,
                prompt_text=scenario.question,
                raw_retrieved_text=rag_text_block,
                generated_response=cleaned_response,
                duration_seconds=duration,
                rag_recall_score=rag_score,
                semantic_score=sem_score,
                faithfulness_score=faith_score if faith_score != -1.0 else None,
                relevance_score=rel_score if rel_score != -1.0 else None,
                extra_metrics={"eval_success_rate": (faith_success + rel_success) / 2.0 if eval_attempts > 0 else 0.0,
                               **traj_metrics}
            )

        run_record.eval_success_rate = total_eval_success / eval_attempts if eval_attempts > 0 else 0.0
        run_record.save()
        
        log_callback(f"Long-Context Run Complete.")
        return run_record

    finally:
        service_registry._rag_service = original_rag_service
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)
