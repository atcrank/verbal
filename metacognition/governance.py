import logging
from celery import shared_task

logger = logging.getLogger(__name__)

class PromotionPolicy:
    """
    Evaluates whether an artifact (Blueprint or Tool) meets the 
    criteria for promotion to production.
    """
    
    @classmethod
    def can_promote_blueprint(cls, blueprint_id: int) -> tuple[bool, str]:
        from benchmarking.models import BenchmarkRun
        
        runs = BenchmarkRun.objects.filter(experiment__blueprint_id=blueprint_id)
        if not runs.exists():
            return False, "Blueprint has never been benchmarked."
            
        recent_runs = runs.order_by('-timestamp')[:3]
        
        # Simple policy: average semantic score across last 3 runs must be > 0.8
        scores = [r.average_semantic_score for r in recent_runs if r.average_semantic_score]
        if not scores:
            return False, "Benchmark runs completed but no semantic scores available."
            
        avg_score = sum(scores) / len(scores)
        if avg_score >= 0.8:
            return True, f"Passed. Average semantic score {avg_score:.2f} >= 0.8"
        else:
            return False, f"Failed. Average semantic score {avg_score:.2f} < 0.8"
            
    @classmethod
    def can_promote_tool(cls, tool_id: int) -> tuple[bool, str]:
        # Tool promotion logic (could involve a dry-run test suite)
        # For now, requires manual review
        return False, "Tool promotion requires manual admin review."


@shared_task
def night_manager_task():
    """
    Celery Beat task that runs during low-usage hours.
    Discovers unpromoted blueprints and runs them against standard benchmarks
    so users have data to decide on promotion.
    """
    logger.info("Night Manager starting...")
    from .models import CognitiveBlueprint
    from benchmarking.models import Experiment, BenchmarkScenario
    from benchmarking.runner import run_experiment_async
    
    # 1. Find unpromoted blueprints (we don't actually have an is_promoted flag on Blueprint yet, 
    # but we can check for blueprints without BenchmarkRuns)
    # This is a simplified prototype logic.
    
    # Check all blueprints for recent benchmark runs
    blueprints = CognitiveBlueprint.objects.all()
    
    experiment_count = 0
    for bp in blueprints:
        # Create an experiment if one doesn't exist for this blueprint
        experiment, created = Experiment.objects.get_or_create(
            name=f"Night Manager: {bp.name}",
            defaults={
                'generation_target': 'blueprint',
                'blueprint': bp,
                'rag_strategy': 'Agentic', # default strategy
            }
        )
        
        if created:
            logger.info(f"Night Manager created new experiment for {bp.name}")
            
            # Link to a standard scenario group (assuming one named 'Standard Core' exists)
            from benchmarking.models import ScenarioGroup
            standard_group = ScenarioGroup.objects.filter(name='Standard Core').first()
            if standard_group:
                experiment.scenario_groups.add(standard_group)
                
            # Queue the experiment to run
            run_experiment_async.delay(experiment.id)
            experiment_count += 1
            
    logger.info(f"Night Manager finished. Queued {experiment_count} experiments.")
