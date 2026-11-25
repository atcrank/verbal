from django.tasks import task
from background_resources.models import Document
from benchmarking.generators import generate_scenarios_for_document


@task
def task_generate_benchmarks(document_ids, stride=5):
    """Background task to generate synthetic QA scenarios."""
    docs = Document.objects.filter(id__in=document_ids)
    for doc in docs:
        generate_scenarios_for_document(doc, stride=stride)


@task
def sweep_benchmark_generation():
    """
    Periodic task to generate synthetic scenarios for recently uploaded documents.
    """
    from background_resources.models import Document

    # Heuristic: Process the 3 most recently uploaded documents
    recent_docs = Document.objects.order_by('-uploaded_at')[:3]
    doc_ids = list(recent_docs.values_list('id', flat=True))
    if doc_ids:
        task_generate_benchmarks.enqueue(doc_ids)
    return f"Queued benchmark generation for recent documents: {doc_ids}"


@task
def task_train_lora(dataset_id, model_id='google/gemma-4-E2B-it', epochs=3, rank=16, batch_size=2):
    """
    Background task to train a LoRA adapter from a FineTuningDataset.
    """
    from django.core.management import call_command
    from django.conf import settings
    import os
    from benchmarking.models import FineTuningDataset

    try:
        dataset = FineTuningDataset.objects.get(id=dataset_id)

        # Determine output directory
        lora_name = f"lora_{dataset.name.replace(' ', '_').lower()}_{dataset.id}"
        output_path = os.path.join(settings.BASE_DIR, "lora_adapters", lora_name)

        call_command(
            'train_lora',
            dataset=dataset.file_path,
            output=output_path,
            model=model_id,
            epochs=epochs,
            rank=rank,
            batch_size=batch_size
        )
        return f"Successfully trained LoRA and saved to {output_path}"
    except Exception as e:
        import traceback
        logger = __import__('logging').getLogger(__name__)
        logger.error(f"Failed to train LoRA for dataset {dataset_id}:\n{traceback.format_exc()}")
        raise e


@task
def task_calculate_dataset_metrics(dataset_id):
    """
    Background task to compute metrics and semantic diversity for a FineTuningDataset.
    """
    import os
    import json
    import numpy as np
    from benchmarking.models import FineTuningDataset
    try:
        dataset = FineTuningDataset.objects.get(id=dataset_id)
        if not os.path.exists(dataset.file_path):
            return "File not found"

        example_count = 0
        total_tokens = 0
        questions = []

        with open(dataset.file_path, 'r', encoding='utf-8') as f:
            for line in f:
                if not line.strip():
                    continue
                example_count += 1
                try:
                    data = json.loads(line)
                    # Approximate token count for entire line
                    total_tokens += int(len(line.split()) * 1.3)

                    # Extract the question for semantic diversity calculation
                    if dataset.format == 'sharegpt':
                        for msg in data.get('conversations', []):
                            if msg.get('from') == 'human':
                                questions.append(msg.get('value', ''))
                    elif dataset.format == 'openai':
                        for msg in data.get('messages', []):
                            if msg.get('role') == 'user':
                                questions.append(msg.get('content', ''))
                except Exception:
                    pass

        # Update metrics
        dataset.example_count = example_count
        dataset.total_tokens = total_tokens

        # Estimate training time: (tokens * 3 epochs) / (~2000 tokens/sec * 60 seconds)
        # We'll use a conservative 1000 tokens/sec for a small model on a standard consumer GPU
        tokens_per_minute = 1000 * 60
        dataset.estimated_training_minutes = int((total_tokens * 3) / tokens_per_minute)

        # Semantic Diversity
        if len(questions) > 1:
            try:
                from django.conf import settings
                from llm_api.torch_compat import apply_torch_compatibility_shims
                apply_torch_compatibility_shims()

                from sentence_transformers import SentenceTransformer
                from sklearn.metrics.pairwise import cosine_distances
                from background_resources.rag_service import get_embedding_model_name

                # Keep worker/web/scheduler embeddings on CPU (Invariant 4: zero VRAM outside inference)
                active_role = getattr(settings, "VERBAL_ROLE", None) or os.environ.get("VERBAL_ROLE", "standalone")
                st_device = os.environ.get("REASON_EMBEDDING_DEVICE") or (
                    "cpu" if active_role in ("web", "worker", "scheduler") else None
                )
                st_kwargs = {"device": st_device} if st_device else {}

                # Use configured embedding model (cache-first to avoid uncommanded HF network checks)
                emb_name = get_embedding_model_name()
                try:
                    model = SentenceTransformer(emb_name, local_files_only=True, **st_kwargs)
                except ImportError:
                    raise
                except Exception:
                    model = SentenceTransformer(emb_name, **st_kwargs)
                # For very large datasets, cap the sample size to prevent OOM or extremely long execution
                sample_questions = questions[:1000]

                embeddings = model.encode(sample_questions)
                # Compute pairwise cosine distances
                distances = cosine_distances(embeddings)
                # Average distance between all distinct pairs
                mask = np.ones(distances.shape, dtype=bool)
                np.fill_diagonal(mask, 0)
                avg_distance = distances[mask].mean()

                dataset.semantic_diversity_score = float(avg_distance)
            except Exception as e:
                logger = __import__('logging').getLogger(__name__)
                logger.error(f"Failed to calculate semantic diversity: {e}")
        else:
            dataset.semantic_diversity_score = 0.0

        dataset.save()
        return f"Metrics calculated for dataset {dataset_id}"
    except Exception as e:
        import traceback
        logger = __import__('logging').getLogger(__name__)
        logger.error(f"Failed to calculate metrics for dataset {dataset_id}:\n{traceback.format_exc()}")
        raise e


@task
def task_execute_benchmark_run(run_id: int):
    """
    Background task to execute a benchmark run asynchronously without holding a web worker or SSE connection.
    Populates BenchmarkResult records and updates average scores on the BenchmarkRun model.
    """
    import logging
    from django.db import connection, close_old_connections
    from benchmarking.models import BenchmarkRun, BenchmarkResult
    from benchmarking.views import _execute_single_scenario
    from llm_api.apps import service_registry

    logger = logging.getLogger(__name__)

    try:
        run = BenchmarkRun.objects.get(id=run_id)
    except BenchmarkRun.DoesNotExist:
        logger.warning(f"task_execute_benchmark_run: Run {run_id} does not exist.")
        return f"Run {run_id} does not exist."

    experiment = run.experiment
    scenarios = []
    if experiment.scenario_group:
        scenarios = list(experiment.scenario_group.scenarios.all())
    elif run.corpus and hasattr(run.corpus, "benchmarkscenario_set"):
        scenarios = list(run.corpus.benchmarkscenario_set.all())

    if not scenarios:
        logger.info(f"task_execute_benchmark_run: No scenarios for Run {run_id}.")
        return f"No scenarios for Run {run_id}."

    existing_results = list(BenchmarkResult.objects.filter(run=run).select_related("scenario"))
    if len(existing_results) >= len(scenarios):
        return f"Run {run_id} already completed ({len(existing_results)} results)."

    ai_service = getattr(service_registry, "ai_service", None)
    created_results = list(existing_results)

    config = run.configuration_snapshot or experiment.configuration or {}
    multi_turn_mode = config.get("multi_turn_mode", "isolated").lower()
    conversation_ref = None
    if config.get("generation_target") == "blueprint" and multi_turn_mode == "chained":
        from llm_api.models import Conversation
        from django.contrib.auth import get_user_model
        User = get_user_model()
        bench_user = User.objects.filter(username="NightManager").first() or User.objects.first()
        if bench_user:
            conversation_ref = Conversation.objects.create(
                user=bench_user,
                title=f"Benchmark Run #{run.id}: {experiment.name[:45]}"
            )

    for scenario in scenarios:
        already_done = any(r.scenario_id == scenario.id for r in created_results)
        if not already_done:
            if not connection.in_atomic_block:
                close_old_connections()
            res = _execute_single_scenario(run, scenario, ai_service, conversation_ref=conversation_ref)
            created_results.append(res)
            if not connection.in_atomic_block:
                close_old_connections()

    if created_results:
        run.average_rag_score = sum(r.rag_score or 0 for r in created_results) / len(created_results)
        run.average_semantic_score = sum(r.semantic_score or 0 for r in created_results) / len(created_results)
        run.average_faithfulness = sum(r.faithfulness or 0 for r in created_results) / len(created_results)
        run.average_relevance = sum(r.relevance or 0 for r in created_results) / len(created_results)
        run.save()

    return f"Run {run_id} finished: {len(created_results)}/{len(scenarios)} scenarios executed."


@task
def task_execute_investigation_matrix(investigation_id: int):
    """
    Background task to execute all runs across experiments in an Investigation matrix.
    """
    import logging
    from benchmarking.models import Investigation, BenchmarkRun

    logger = logging.getLogger(__name__)
    try:
        investigation = Investigation.objects.get(id=investigation_id)
    except Investigation.DoesNotExist:
        logger.warning(f"task_execute_investigation_matrix: Investigation {investigation_id} does not exist.")
        return f"Investigation {investigation_id} does not exist."

    experiments = list(investigation.experiments.all().order_by("id"))
    completed_runs = []
    for exp in experiments:
        run = BenchmarkRun.objects.filter(experiment=exp).order_by("-timestamp").first()
        if not run and exp.corpus:
            run = BenchmarkRun.objects.create(
                experiment=exp,
                corpus=exp.corpus,
                configuration_snapshot=exp.configuration,
            )
        if run:
            task_execute_benchmark_run.func(run.id)
            completed_runs.append(run.id)

    return f"Investigation {investigation_id} executed: {len(completed_runs)} runs processed."
