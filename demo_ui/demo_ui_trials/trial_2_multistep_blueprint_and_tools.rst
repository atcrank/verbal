Tutorial 2: Multi-Step Cognitive Reasoning and Sandboxed Tool Execution
======================================================================

We imagine a researcher who has the following task and resources:
An autonomous robotics research team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason interface to explore existing literature, formulate causal hypotheses, evaluate statistical and combinatory factor trade-offs, compute factorial parameters in the sandbox, and structure the domain knowledge graph.

In this second tutorial, the researcher investigates a multi-factor engineering trade-off:

1. **Experimental Formulation**: Poses a 6-factor combinatory study ($3 \text{ sensor payloads} \times 2 \text{ terrain profiles}$) with a 960Wh usable battery reserve and a 120-minute operational mission threshold.
2. **Strict Role Separation**: The researcher does **NOT** write Python code. The researcher describes the causal factors and operational goals in domain terms, instructing Reason to use the ``Reasoning with Code Sandbox`` cognitive blueprint.
3. **Local Model Execution**: Verbal / Reason (Gemma4) formulates the mathematical equations, composes the Python simulation script, executes it inside the Docker sandbox (``verbal_sandbox``), and parses the stdout.
4. **Tabulation & Recommendation**: Gemma4 yields an informative tabulation of operational search durations across all 6 configurations and recommends which factor levels satisfy the experimental criteria.

Execution
---------

First, we set up credentials and seed the ``Reasoning with Code Sandbox`` cognitive blueprint.

    >>> import os, json, time, contextlib, io
    >>> os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    >>> from pathlib import Path
    >>> from django.contrib.auth import get_user_model
    >>> from metacognition.models import CognitiveBlueprint, ReasoningStep, ToolDefinition, bypass_canonical_lock
    >>> from metacognition.seed import seed_tools, seed_reasoning_with_code_sandbox
    >>> from metacognition.tasks import run_blueprint
    >>> from llm_api.models import Conversation, PromptResponseLog
    >>> from demo_ui.demo_ui_trials.helpers import take_ui_screenshot, capture_ai_response, record_ui_doctest_run, timed_step
    >>> from playwright.sync_api import sync_playwright

    >>> User = get_user_model()
    >>> user, _ = User.objects.get_or_create(username="robotics_researcher", defaults={"is_staff": True, "is_superuser": True})
    >>> _ = user.set_password("rescue2026")
    >>> user.save()
    >>> model_name = get_active_model_name()
    >>> trial_t0 = time.perf_counter()

    >>> with bypass_canonical_lock():
    ...     seed_tools(ToolDefinition)
    ...     seed_reasoning_with_code_sandbox(CognitiveBlueprint, ReasoningStep, ToolDefinition)
    >>> bp = CognitiveBlueprint.objects.get(name="Reasoning with Code Sandbox")
    >>> with bypass_canonical_lock():
    ...     for s in bp.steps.all():
    ...         s.max_new_tokens = 1500
    ...         s.save()
    >>> print(f"Blueprint configured: '{bp.name}', steps: {bp.steps.count()}")
    Blueprint configured: 'Reasoning with Code Sandbox', steps: 2

Step 1: Posing Factorial Engineering Trade-Off Inquiry
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher formulates a rigorous combinatory study across 3 sensor payloads and 2 terrain configurations.

    >>> prompt = (
    ...     "We are designing an empirical intervention trial for an autonomous tracked firefighting robot operating in a collapsed multi-story structure.\n"
    ...     "Power system parameters:\n"
    ...     "- Battery capacity: 1200Wh pack with a 20% emergency safety reserve (80% usable energy = 960Wh usable).\n"
    ...     "Experimental factors:\n"
    ...     "- Sensor payload draw: Baseline (25W), Standard Multi-spectral (75W), Heavy LiDAR+Thermal+Compute (110W).\n"
    ...     "- Locomotion power across 2 terrains: Smooth Concrete (280W) and Heavy Rubble/Debris (460W).\n"
    ...     "Task:\n"
    ...     "Formulate the trade-off calculation and write a clean Python script using the python_sandbox tool to compute the exact "
    ...     "operational search duration in minutes for all 6 factor combinations. Output an informative formatted tabulation of results, "
    ...     "and recommend factor levels for a 2-hour (120 minute) minimum search mission."
    ... )

Step 2: Browser Navigation & Blueprint Selection
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher connects to the Reason Demo UI, selects the ``Reasoning with Code Sandbox`` cognitive blueprint, and inputs the domain inquiry.

    >>> steps_recorded = []
    >>> pw = sync_playwright().start()
    >>> browser = pw.chromium.launch(headless=True)
    >>> page = browser.new_page(viewport={"width": 1280, "height": 800})
    >>> authenticate_page(page, user)

    >>> with timed_step("Blueprint Selection & Prompt Submission", steps_recorded) as step:
    ...     _ = page.goto(f"{live_server_url}/demo/")
    ...     _ = page.wait_for_selector("#chat-form", timeout=6000)
    ...     _ = page.select_option('select[name="blueprint_id"]', str(bp.id))
    ...     page.fill('textarea[name="user_prompt"]', prompt)
    ...     img1 = take_ui_screenshot(page, "trial_2_prompt_submitted")
    ...     step["image"] = img1
    ...     step["researcher_thinking"] = (
    ...         "A simple mental estimate cannot reliably determine whether thermal vs. LiDAR payloads remain "
    ...         "viable when traversing rubble. The interaction between locomotion resistance and sensor draw is "
    ...         "combinatory. We delegate the mathematical calculation to Verbal / Reason's computational blueprint."
    ...     )
    ...     step["researcher_inquiry"] = prompt
    ...     step["blueprint_name"] = bp.name
    ...     step["description"] = f"Selected '{bp.name}' blueprint and submitted 6-factor trade-off inquiry."

Step 3: Real Blueprint Execution via run_blueprint()
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher dispatches the problem. Verbal / Reason (Gemma4) composes the Python simulation script, executes it in the Docker sandbox (``verbal_sandbox``), and synthesizes an informative tabulation.

    >>> with timed_step("Blueprint Execution (Gemma4 + Docker Sandbox)", steps_recorded) as step:
    ...     captured_stdout = io.StringIO()
    ...     with contextlib.redirect_stdout(captured_stdout):
    ...         result = run_blueprint(bp.id, prompt, user_id=user.id)
    ...     step["model_name"] = model_name
    ...     step["blueprint_name"] = bp.name
    ...     final_response = result.get("final_response", "No output.")
    ...     monologue = result.get("internal_monologue", [])
    ...     step["response_text"] = final_response
    ...     
    ...     # Extract Python code composed by Gemma4 from workspace
    ...     cid = result.get("conversation_id")
    ...     workspace_dir = Path("workspaces") / str(cid) if cid else None
    ...     gen_code = ""
    ...     if workspace_dir and workspace_dir.exists():
    ...         for code_file in sorted(workspace_dir.glob("*.py")):
    ...             gen_code = code_file.read_text(encoding="utf-8")
    ...             break
    ...     if not gen_code and monologue:
    ...         # Check tool calls in monologue
    ...         for entry in monologue:
    ...             out_text = str(entry.get("output", ""))
    ...             if "python_sandbox" in out_text and "code" in out_text:
    ...                 gen_code = out_text
    ...                 break
    ...     step["code_text"] = gen_code
    ...     
    ...     # Extract sandbox output from monologue or redirect
    ...     sb_out = ""
    ...     for entry in monologue:
    ...         tr = str(entry.get("tool_result", ""))
    ...         if "Sandbox Execution" in tr or "Output:" in tr:
    ...             sb_out = tr
    ...             break
    ...     step["sandbox_output"] = sb_out or captured_stdout.getvalue()
    ...     step["description"] = (
    ...         f"Blueprint executed {len(monologue)} reasoning steps via {model_name}. "
    ...         f"Final response: {len(final_response)} characters. "
    ...         f"Route: {result.get('route_to', 'unknown')}."
    ...     )

    >>> has_response = bool(result.get("final_response")) and result["final_response"] != "No output."
    >>> print(f"Blueprint produced response: {has_response}")
    Blueprint produced response: True

Step 4: Visualizing the Result in the Demo UI
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher views the resulting conversation in the Reason Demo UI, inspecting the rendered tabulation.

    >>> with timed_step("Result Visualization & Researcher Evaluation", steps_recorded) as step:
    ...     cid = result.get("conversation_id")
    ...     if cid:
    ...         _ = page.goto(f"{live_server_url}/demo/?conversation_id={cid}")
    ...         _ = page.wait_for_selector(".chat-message.ai .bubble", timeout=6000)
    ...         ui_response = capture_ai_response(page)
    ...     else:
    ...         ui_response = "(no conversation created)"
    ...     img2 = take_ui_screenshot(page, "trial_2_tool_output")
    ...     step["image"] = img2
    ...     step["response_text"] = ui_response
    ...     step["description"] = f"Rendered LLM-generated response ({len(ui_response)} chars) in Demo UI."
    ...     step["researcher_evaluation"] = (
    ...         "The tabulation generated by Gemma4 reveals a decisive operational finding: "
    ...         "On smooth concrete, all three sensor packages comfortably exceed the 120-minute threshold "
    ...         "(Baseline: 188 min, Multi-spectral: 162 min, LiDAR+Compute: 147 min). "
    ...         "However, on heavy rubble (460W locomotion), NO configuration meets 120 minutes "
    ...         "(Baseline: 118.7 min, Multi-spectral: 107.6 min, LiDAR+Compute: 101.0 min). "
    ...         "To achieve a 2-hour mission on rubble, the robot cannot carry high-draw comms/sensors directly on board; "
    ...         "it requires lightweight ad-hoc deployable breadcrumb relays. This motivates Trial 3."
    ...     )

    >>> browser.close()
    >>> pw.stop()

Step 5: Report Generation
^^^^^^^^^^^^^^^^^^^^^^^^^^

    >>> total_duration = round(time.perf_counter() - trial_t0, 2)
    >>> report_path = record_ui_doctest_run(
    ...     "trial_2_multistep_blueprint_and_tools",
    ...     "Trial 2: Multi-Step Cognitive Reasoning & Sandboxed Tools",
    ...     steps_recorded,
    ...     model_name=model_name,
    ...     total_duration_s=total_duration,
    ...     blueprint_result=result,
    ... )
    >>> print(f"- complete attempt: {1 if os.path.exists(report_path) else 0}/1")
    - complete attempt: 1/1
