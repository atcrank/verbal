import os
import json
from django.conf import settings
from benchmarking.models import ScenarioGroup, BenchmarkScenario

def import_dataset_from_jsonl(filepath: str, group_name: str, 
                               question_key: str = "question",
                               answer_key: str = "ideal_answer",
                               keywords_key: str = None) -> ScenarioGroup:
    """
    Generic importer for JSONL evaluation files.
    Maps fields to BenchmarkScenario entries.
    """
    group, _ = ScenarioGroup.objects.get_or_create(name=group_name)
    
    with open(filepath, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
                
            question = data.get(question_key, "")
            ideal_answer = data.get(answer_key, "")
            keywords = data.get(keywords_key, []) if keywords_key else []
            
            if question and ideal_answer:
                scenario = BenchmarkScenario.objects.create(
                    question=question,
                    ideal_answer=ideal_answer,
                    expected_keywords=keywords
                )
                group.scenarios.add(scenario)
                
    return group

def import_sciassess(subset: str = "all") -> ScenarioGroup:
    """
    Loads SciAssess evaluation data and creates a ScenarioGroup.
    
    Attempts to load from HuggingFace datasets library.
    Falls back to local fixtures in benchmarking/fixtures/ if not available.
    """
    group_name = f"SciAssess ({subset})"
    group, created = ScenarioGroup.objects.get_or_create(name=group_name)
    
    # If the group already has scenarios, we can assume it was previously imported.
    if not created and group.scenarios.exists():
        return group
        
    try:
        import datasets
        # Load from huggingface datasets
        dataset = datasets.load_dataset("SciAssess/SciAssess", split="test")
        
        for item in dataset:
            scenario = BenchmarkScenario.objects.create(
                question=item.get("question", ""),
                ideal_answer=item.get("answer", ""),
                expected_keywords=[]
            )
            group.scenarios.add(scenario)
            
    except ImportError:
        # Fall back to local fixtures
        fixture_path = os.path.join(settings.BASE_DIR, "benchmarking", "fixtures", "sciassess.jsonl")
        if os.path.exists(fixture_path):
            return import_dataset_from_jsonl(
                fixture_path, group_name, question_key="question", answer_key="answer"
            )
        else:
            raise RuntimeError("HuggingFace 'datasets' library is not installed and local fixture not found.")
            
    return group
