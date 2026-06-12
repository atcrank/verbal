Scenario 1.1: The Strawberry Counting Problem
=============================================

This tutorial demonstrates the LLM's ability to recognize a task prone to hallucination, 
bypass its standard generation, and write deterministic Python code in the sandbox to verify the answer.

Execution
---------
We initialize the ``ExecutionPlan`` blueprint to drop the agent directly into the action sandbox.

    >>> from metacognition.tasks import run_blueprint
    >>> from metacognition.models import CognitiveBlueprint
    >>> from django.contrib.auth.models import User
    >>> user, _ = User.objects.get_or_create(username="test_user")
    >>> bp = CognitiveBlueprint.objects.filter(name__startswith="Pipeline: ResearchEvaluation").first()
    
    >>> prompt = (
    ...     "How many 'r's are in the word strawberry? "
    ...     "Write and execute a Python expression in the sandbox to count them, "
    ...     "and return the exact number."
    ... )
    
    >>> import contextlib, io
    >>> with contextlib.redirect_stdout(io.StringIO()):
    ...     result = run_blueprint(bp.id, prompt, user_id=user.id)
    >>> final_answer = result.get("final_response", "").lower()
    
    >>> "3" in final_answer or "three" in final_answer
    True
