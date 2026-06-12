Scenario 1.4: Complex Modeling and Simulation
=============================================

This scenario tests the AI's ability to translate a real-world problem into a mathematical model,
write a simulation script, execute it, and analyze the results. This represents a "threshold" case
that often requires multiple iterations of planning, coding, and debugging arrays.

Execution
---------
We target the full agentic pipeline.

    >>> from metacognition.tasks import run_blueprint
    >>> from metacognition.models import CognitiveBlueprint
    >>> from django.contrib.auth.models import User
    >>> user, _ = User.objects.get_or_create(username="test_user")
    >>> bp = CognitiveBlueprint.objects.filter(name__startswith="Pipeline: ResearchEvaluation").first()

    >>> prompt = (
    ...     "I need to model a predator-prey ecosystem over 100 months using the Lotka-Volterra equations. "
    ...     "The initial population of Rabbits (prey) is 40. Their natural birth rate is 0.1, and the rate at which they are eaten is 0.02. "
    ...     "The initial population of Foxes (predators) is 9. Their growth rate from eating rabbits is 0.01, and their natural death rate is 0.1. "
    ...     "Write a Python script using numpy to simulate this ecosystem using the Euler method with a time step of dt=1 month for 100 steps. "
    ...     "Analyze the results to find the peak population of Rabbits and the peak population of Foxes. "
    ...     "Execute the script and return those two peak values."
    ... )

    >>> import contextlib, io
    >>> with contextlib.redirect_stdout(io.StringIO()):
    ...     result = run_blueprint(bp.id, prompt, user_id=user.id)
    >>> final_answer = result.get("final_response", "").lower()

    >>> "rabbit" in final_answer and "fox" in final_answer
    True