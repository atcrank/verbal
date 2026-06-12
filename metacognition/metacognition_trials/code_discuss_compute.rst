Scenario 1.2: Set Addition and Script I/O
=========================================

This scenario tests the AI's ability to combine general Python knowledge with 
basic script execution and ``stdout`` reading.

Execution
---------

    >>> from metacognition.tasks import run_blueprint
    >>> from metacognition.models import CognitiveBlueprint
    >>> from django.contrib.auth.models import User
    >>> user, _ = User.objects.get_or_create(username="test_user")
    >>> bp = CognitiveBlueprint.objects.filter(name__startswith="Pipeline: ResearchEvaluation").first()
    
    >>> prompt = (
    ...     "How can I add the Python sets {1, 3, 4} and {1, 2}? "
    ...     "Write the code, execute it in the sandbox to print the union, "
    ...     "and provide the result."
    ... )
    
    >>> import contextlib, io
    >>> with contextlib.redirect_stdout(io.StringIO()):
    ...     result = run_blueprint(bp.id, prompt, user_id=user.id)
    >>> final_answer = result.get("final_response", "")
    
    >>> all(str(x) in final_answer for x in [1, 2, 3, 4])
    True