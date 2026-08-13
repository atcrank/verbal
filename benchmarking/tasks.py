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

@shared_task
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

@shared_task
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
                if not line.strip(): continue
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
                from sentence_transformers import SentenceTransformer
                from sklearn.metrics.pairwise import cosine_distances
                
                # Use a small fast model
                model = SentenceTransformer('all-MiniLM-L6-v2')
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