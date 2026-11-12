Tutorial 3: Multi-Step Cognitive Reasoning and Sandboxed Tool Execution
======================================================================

We imagine a researcher who has the following task and resources:
An autonomous robotics research team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason interface to explore existing literature, formulate causal hypotheses, evaluate statistical and combinatory factor trade-offs, compute factorial parameters in the sandbox, and structure the domain knowledge graph.

In this third tutorial, the researcher investigates the 6-factor combinatory engineering space grounded in Trial 2's literature extraction:

1. **Experimental Formulation**: Poses the $3 \times 2$ factorial study (3 sensor payloads $\times$ 2 terrain locomotion profiles) with a 960Wh usable battery reserve and a 120-minute minimum operational mission threshold ($T_{op} \ge 120$).
2. **Strict Role Separation**: The researcher does **NOT** write Python code. The researcher describes the causal factors and operational goals in domain terms, instructing Reason to use the ``Reasoning with Code Sandbox`` cognitive blueprint.
3. **Local Model Execution**: Verbal / Reason composes the Python simulation script ($T_{op} = \frac{E_{usable}}{P_{total}} \times 60$), executes it inside the Docker sandbox (``verbal_sandbox``), and synthesizes the resulting endurance table.
4. **Active Adversarial Verification**: The researcher audits the model's simulation script and recommendation. If an inverted selection logic error occurs (e.g. recommending the minimum duration or a configuration below 120 minutes), the researcher flags the flaw and prompts a corrective turn.
5. **Decisive Causal Finding**: The corrected tabulation proves that on heavy rubble (460W), zero onboard configurations can achieve 120 minutes, mathematically motivating deployable ad-hoc relays in Trial 4.

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
    >>> orig_max_tokens = {s.id: s.max_new_tokens for s in bp.steps.all()}
    >>> with bypass_canonical_lock():
    ...     for s in bp.steps.all():
    ...         s.max_new_tokens = 1500
    ...         s.save()
    >>> print(f"Blueprint configured: '{bp.name}', steps: {bp.steps.count()}")
    Blueprint configured: 'Reasoning with Code Sandbox', steps: 2

Step 1: Posing Factorial Engineering Trade-Off Inquiry
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher formulates a rigorous combinatory study across 3 sensor payloads and 2 terrain configurations derived directly from Trial 2.

    >>> prompt = (
    ...     "We are designing an empirical intervention trial for an autonomous tracked firefighting robot operating in a collapsed multi-story structure.\n"
    ...     "Scenario power parameters (scaled heavy-UGV design informed by Talavera et al. 2023):\n"
    ...     "- Battery capacity: 1200Wh pack with a 20% emergency safety reserve (80% usable energy = 960Wh usable).\n"
    ...     "Experimental factors:\n"
    ...     "- Sensor payload draw: Baseline (25W), Standard Multi-spectral (75W), Heavy LiDAR+Thermal+Compute (110W).\n"
    ...     "- Locomotion power across 2 terrains: Smooth Concrete (280W) and Heavy Rubble/Debris (460W).\n"
    ...     "Task:\n"
    ...     "Formulate the trade-off calculation and write a clean Python script using the python_sandbox tool to compute the exact "
    ...     "operational search duration in minutes for all 6 factor combinations. Filter strictly for configurations meeting the "
    ...     "120-minute minimum operational requirement (T_op >= 120), and recommend the highest-capability sensor package that meets this constraint."
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
    ...     img1 = take_ui_screenshot(page, "trial_3_prompt_submitted")
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
The researcher dispatches the problem. Verbal / Reason composes the Python simulation script, executes it in the Docker sandbox (``verbal_sandbox``), and synthesizes a tabulation.

    >>> with timed_step("Blueprint Execution (Local Model + Docker Sandbox)", steps_recorded) as step:
    ...     captured_stdout = io.StringIO()
    ...     with contextlib.redirect_stdout(captured_stdout):
    ...         result = run_blueprint(bp.id, prompt, user_id=user.id)
    >>> with bypass_canonical_lock():
    ...     for s in bp.steps.all():
    ...         if s.id in orig_max_tokens:
    ...             s.max_new_tokens = orig_max_tokens[s.id]
    ...             s.save()
    ...     step["model_name"] = model_name
    ...     step["blueprint_name"] = bp.name
    ...     final_response = result.get("final_response", "No output.")
    ...     monologue = result.get("internal_monologue", [])
    ...     step["response_text"] = final_response
    ...     
    ...     # Extract Python code composed by local model from conversation workspace
    ...     cid = result.get("conversation_id")
    ...     workspace_dir = None
    ...     if cid:
    ...         try:
    ...             workspace_dir = Path(Conversation.objects.get(id=cid).get_workspace_dir())
    ...         except Exception:
    ...             workspace_dir = Path("workspaces") / "conversations" / str(cid)
    ...     gen_code = ""
    ...     if workspace_dir and workspace_dir.exists():
    ...         for code_file in sorted(workspace_dir.glob("*.py")):
    ...             gen_code = code_file.read_text(encoding="utf-8")
    ...             break
    ...     if not gen_code and monologue:
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

Step 4: Active Adversarial Verification & Error Catching
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher inspects the model's generated code and recommendation trace to ensure mathematical and logical integrity.

    >>> with timed_step("Adversarial Verification & Corrective Follow-Up", steps_recorded) as step:
    ...     cid = result.get("conversation_id")
    ...     if cid:
    ...         _ = page.goto(f"{live_server_url}/demo/?conversation_id={cid}")
    ...         _ = page.wait_for_selector(".chat-message.ai .bubble", timeout=6000)
    ...         ui_response = capture_ai_response(page)
    ...     else:
    ...         ui_response = final_response
    ...     img2 = take_ui_screenshot(page, "trial_3_tool_output")
    ...     step["image"] = img2
    ...     step["response_text"] = ui_response
    ...     step["model_name"] = model_name
    ...
    ...     combined_trace = f"{sb_out}\n{final_response}\n{ui_response}".lower()
    ...     sandbox_ran_ok = "sandbox execution succeeded" in sb_out.lower() and "syntaxerror" not in sb_out.lower()
    ...     has_syntax_or_help_error = (
    ...         "syntaxerror" in combined_trace
    ...         or "share your sandbox code" in combined_trace
    ...         or "refusing to execute empty" in combined_trace
    ...     )
    ...     has_inversion_bug = "idxmin" in gen_code.lower() or ("101." in ui_response.lower() and "recommend" in ui_response.lower())
    ...     expected_nums = ["188.", "162.", "147.", "118.", "107.", "101."]
    ...     matched_nums = [tok for tok in expected_nums if tok in combined_trace]
    ...     has_all_endurance = len(matched_nums) >= 4
    ...     if sandbox_ran_ok and not has_syntax_or_help_error and not has_inversion_bug and has_all_endurance:
    ...         step["adversarial_audit"] = (
    ...             f"LOGIC AUDIT PASSED: Sandbox script executed cleanly (matched values: {matched_nums}) "
    ...             "and filtered configurations using T_op >= 120 minutes."
    ...         )
    ...         step["researcher_evaluation"] = (
    ...             f"Observed model output matched expected endurance combinations ({matched_nums}) and executed cleanly."
    ...         )
    ...     else:
    ...         reasons = []
    ...         if not sandbox_ran_ok:
    ...             reasons.append("sandbox did not succeed cleanly")
    ...         if has_syntax_or_help_error:
    ...             reasons.append("script SyntaxError or error-recovery fallback response detected")
    ...         if has_inversion_bug:
    ...             reasons.append("inverted duration objective (idxmin / sub-120 min recommendation)")
    ...         if not has_all_endurance:
    ...             reasons.append(f"missing calculated endurance durations (found only {matched_nums})")
    ...         step["adversarial_audit"] = f"LOGIC AUDIT FAILED: {'; '.join(reasons)}."
    ...         corr_prompt = (
    ...             "Audit correction required: compute T_op = (960 / (sensor_w + terrain_w)) * 60 in Python "
    ...             "for all 6 combinations (25, 75, 110 W sensors x 280, 460 W terrains) with zero syntax errors, "
    ...             "and list only configurations with T_op >= 120 min."
    ...         )
    ...         step["corrective_inquiry"] = corr_prompt
    ...         if cid:
    ...             page.fill('textarea[name="user_prompt"]', corr_prompt)
    ...             page.click("#send-btn")
    ...             _ = page.wait_for_function(
    ...                 "() => document.querySelectorAll('#chat-history .chat-message.ai .bubble').length >= 2",
    ...                 timeout=3600000,
    ...             )
    ...             corr_resp = capture_ai_response(page)
    ...             step["response_text"] = f"[Initial UI Bubble]:\n{ui_response}\n\n[Corrective Turn Response]:\n{corr_resp}"
    ...             corr_matched = [tok for tok in expected_nums if tok in corr_resp.lower()]
    ...             step["researcher_evaluation"] = (
    ...                 f"Initial blueprint failed audit ({'; '.join(reasons)}). Executed live corrective UI turn; "
    ...                 f"corrective response matched {corr_matched}."
    ...             )
    ...         else:
    ...             step["researcher_evaluation"] = f"Blueprint failed audit ({'; '.join(reasons)})."

    >>> browser.close()
    >>> pw.stop()

Step 5: Report Generation
^^^^^^^^^^^^^^^^^^^^^^^^^

    >>> expected_outcome = (
    ...     "Compute T_op = (960 / total_w) * 60 in Python with zero errors: "
    ...     "Smooth Concrete (280W) -> Baseline 305W = 188.8 min, Multi-spectral 355W = 162.2 min (recommended highest valid), Heavy 390W = 147.7 min; "
    ...     "Heavy Rubble (460W) -> Baseline 485W = 118.7 min, Multi-spectral 535W = 107.6 min, Heavy 570W = 101.0 min (all < 120 min)."
    ... )
    >>> checks = [
    ...     {"label": "python_sandbox ran cleanly (RC=0)", "passed": sandbox_ran_ok, "detail": ""},
    ...     {"label": "no SyntaxError or error-help fallback", "passed": not has_syntax_or_help_error, "detail": ""},
    ...     {"label": "endurance table values computed (>=4/6 cells)", "passed": has_all_endurance, "detail": f"matched={matched_nums}"},
    ...     {"label": "no inverted idxmin recommendation", "passed": not has_inversion_bug, "detail": ""},
    ... ]
    >>> total_duration = round(time.perf_counter() - trial_t0, 2)
    >>> report_path = record_ui_doctest_run(
    ...     "trial_3_multistep_blueprint_and_tools",
    ...     "Trial 3: Multi-Step Cognitive Reasoning & Sandboxed Tools",
    ...     steps_recorded,
    ...     model_name=model_name,
    ...     total_duration_s=total_duration,
    ...     blueprint_result=result,
    ...     scenario_description=(
    ...         "The researcher investigates a 6-factor combinatory space in the Docker sandbox using the "
    ...         "'Reasoning with Code Sandbox' blueprint, then runs programmatic and UI verification over "
    ...         "actual sandbox outputs and synthesized numbers."
    ...     ),
    ...     expected_outcome=expected_outcome,
    ...     checks=checks,
    ... )
    >>> print(f"- complete attempt: {1 if os.path.exists(report_path) else 0}/1")
    - complete attempt: 1/1
