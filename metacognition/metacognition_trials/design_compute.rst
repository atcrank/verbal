Scenario 1.3: Monte Carlo Simulation
====================================

This advanced scenario tests mathematical modeling, the use of external libraries (``numpy``), 
and multi-step reasoning. It requires the AI to first draft a strategy before writing code.

Execution
---------
We target the auto-generated pipeline that links the ``StrategicPlan`` node directly to the ``ExecutionPlan``.

    >>> from metacognition.tasks import run_blueprint
    >>> from metacognition.models import CognitiveBlueprint
    >>> from django.contrib.auth.models import User
    >>> user, _ = User.objects.get_or_create(username="test_user")
    >>> bp = CognitiveBlueprint.objects.filter(name__startswith="Pipeline: ResearchEvaluation").first()
    
    >>> prompt = (
    ...     "If we toss a fair coin to decide whether (HEADS) to roll a D6 (six-sided dice) "
    ...     "or (TAILS) a D20 (20-sided dice), I want to use a simple monte carlo model to compute "
    ...     "the expected average value and the standard deviation for a reasonably informative number of trials. "
    ...     "Write the simulation script, run it, and tell me the mean and standard deviation."
    ... )
    
    >>> import contextlib, io
    >>> with contextlib.redirect_stdout(io.StringIO()):
    ...     result = run_blueprint(bp.id, prompt, user_id=user.id)
    >>> final_answer = result.get("final_response", "")
    
    >>> "7" in final_answer or "7.0" in final_answer
    True