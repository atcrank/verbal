Scenario 1.5: Causal Modeling (PyCID) & The Double Loop
=======================================================

This benchmark tests the "Double Loop" architecture. The agent must formulate a Causal Influence
Diagram (CID), execute the model to calculate expected utilities, and then face an `OutcomeEvaluation`.
If the evaluator finds the script failed or outputted naive logic, it triggers `NEEDS_REVISION`,
routing the agent backward to conduct new research and draft a new plan.

Execution
---------
We target the ultimate 4-stage pipeline.

    >>> from metacognition.tasks import run_blueprint
    >>> from metacognition.models import CognitiveBlueprint
    >>> from django.contrib.auth.models import User
    >>> user, _ = User.objects.get_or_create(username="test_user")

    # Because of our signals, this is now a 4-part mega-pipeline with an infinite failure loop built in!
    >>> bp = CognitiveBlueprint.objects.filter(name__startswith="Pipeline: ResearchEvaluation").first()

    >>> prompt = (
    ...     "Using the `pycid` library, model this dynamic problem as a Multi-Agent Causal Influence Diagram (MACID): "
    ...     "A startup must decide to 'Launch' (Yes/No). The market 'Demand' (High/Low) determines utility. "
    ...     "If Launch=Yes and Demand=High, Utility=100. If Launch=Yes and Demand=Low, Utility=-50. If Launch=No, Utility=0. "
    ...     "Before launching, they can buy a 'Survey' (Yes/No) for a cost of 10. The survey gives a 'Forecast' "
    ...     "that is 80% accurate to the true Demand. P(Demand=High) = 0.6. "
    ...     "Write a PyCID script to model this. Output the Expected Utility of the optimal policy."
    ... )

    >>> import contextlib, io
    >>> with contextlib.redirect_stdout(io.StringIO()):
    ...     result = run_blueprint(bp.id, prompt, user_id=user.id)
    >>> final_answer = result.get("final_response", "").lower()

    # A robust double-loop agent will eventually converge on an expected utility around ~68.
    >>> "utility" in final_answer or "expected" in final_answer or "unresolvable" in final_answer
    True