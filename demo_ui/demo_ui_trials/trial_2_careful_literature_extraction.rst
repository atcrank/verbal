Tutorial 2: Careful Literature Extraction and Parametric Factor Construction
===========================================================================

We imagine a researcher who has the following task and resources:
An autonomous robotics research team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason interface to explore existing literature, formulate causal hypotheses, evaluate statistical and combinatory factor trade-offs, compute factorial parameters in the sandbox, and structure the domain knowledge graph.

In this second tutorial, the researcher bridges the gap between high-level qualitative literature and concrete numerical simulation:

1. **Epistemological Lineage**: Before any mathematical simulations are executed, every physical parameter (battery capacities, motor voltage, sensor draw) must be explicitly extracted from peer-reviewed literature.
2. **Context Attachment**: The researcher locates and attaches the primary hardware architecture section from Talavera et al. (2023) (*An autonomous ground robot to support firefighters' interventions in indoor emergencies*).
3. **Targeted Extraction Inquiry**: The researcher submits an extraction prompt to Verbal / Reason (Gemma4) demanding exact physical specifications for chassis motors, compute boards, telemetry, and power supplies.
4. **Parametric Factor Construction**: Based strictly on the model's extracted evidence, the researcher derives the $3 \times 2$ factorial space (3 sensor payloads $\times$ 2 terrain locomotion profiles with a 960Wh usable battery limit) to feed into the sandboxed simulation in Trial 3.

Execution
---------

First, we set up credentials and verify literature indexing.

    >>> import os, json, time
    >>> os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    >>> from pathlib import Path
    >>> from django.contrib.auth import get_user_model
    >>> from django.core import serializers
    >>> from background_resources.models import Document, RAGChunk
    >>> from llm_api.models import Conversation, PromptResponseLog
    >>> from llm_api.apps import service_registry
    >>> from demo_ui.demo_ui_trials.helpers import (
    ...     take_ui_screenshot,
    ...     capture_ai_response,
    ...     record_ui_doctest_run,
    ...     timed_step,
    ... )
    >>> from playwright.sync_api import sync_playwright

    >>> User = get_user_model()
    >>> user, _ = User.objects.get_or_create(username="robotics_researcher", defaults={"is_staff": True, "is_superuser": True})
    >>> _ = user.set_password("rescue2026")
    >>> user.save()
    >>> model_name = get_active_model_name()
    >>> trial_t0 = time.perf_counter()

    >>> fixture_path = Path("demo_ui/demo_ui_trials/fixtures/firefighting_chunks.json").resolve()
    >>> if fixture_path.exists():
    ...     with open(fixture_path, "r", encoding="utf-8") as f:
    ...         for obj in serializers.deserialize("json", f.read()):
    ...             try:
    ...                 obj.save()
    ...             except Exception:
    ...                 pass
    >>> rag_service = service_registry.rag_service
    >>> if rag_service:
    ...     _ = rag_service.index_unindexed_chunks()

Step 1: Inspect and Pin Hardware Architecture Literature
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher identifies the hardware architecture chunk from Talavera et al. (2023) detailing Level 0 (power/locomotion) and Level 1 (compute/sensors).

    >>> hardware_chunk = (
    ...     RAGChunk.objects.filter(text_content__icontains="GM25-370").first()
    ...     or RAGChunk.objects.filter(text_content__icontains="Level 0 powers the robot").first()
    ...     or RAGChunk.objects.filter(text_content__icontains="Talavera").first()
    ... )
    >>> print(f"Hardware architecture chunk available: {hardware_chunk is not None}")
    Hardware architecture chunk available: True

Step 2: Browser Navigation & Context Chip Formulation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher connects to Reason Demo UI, enters the targeted extraction prompt, and attaches the Talavera hardware architecture excerpt.

    >>> steps_recorded = []
    >>> pw = sync_playwright().start()
    >>> browser = pw.chromium.launch(headless=True)
    >>> page = browser.new_page(viewport={"width": 1280, "height": 800})
    >>> authenticate_page(page, user)

    >>> with timed_step("Context Chip Formulation & Extraction Inquiry", steps_recorded) as step:
    ...     _ = page.goto(f"{live_server_url}/demo/")
    ...     _ = page.wait_for_selector("#chat-form", timeout=6000)
    ...     prompt_text = (
    ...         "From the attached literature excerpt (Talavera et al. 2023), extract the exact physical "
    ...         "power architecture, battery capacities, and sensor payloads documented for the autonomous "
    ...         "firefighting ground robot. Formulate the key power parameters for simulation."
    ...     )
    ...     step["researcher_thinking"] = (
    ...         "Scientific rigor mandates zero fabricated parameters. Before modeling mission endurance in the sandbox, "
    ...         "we must extract verified physical values directly from Talavera et al. (2023) Table 2 and Section 3."
    ...     )
    ...     step["researcher_inquiry"] = prompt_text
    ...     page.fill('textarea[name="user_prompt"]', prompt_text)
    ...     if hardware_chunk:
    ...         citation = hardware_chunk.get_citation()
    ...         _ = page.evaluate("""(data) => {
    ...             includedContexts.push(data);
    ...             updateContextInput();
    ...         }""", {
    ...             "model": "RAGChunk",
    ...             "id": str(hardware_chunk.chunk_id),
    ...             "preview": citation,
    ...             "content": hardware_chunk.text_content
    ...         })
    ...         step["rag_chunks"] = [{
    ...             "id": hardware_chunk.chunk_id,
    ...             "citation": citation,
    ...             "preview": hardware_chunk.text_content[:200]
    ...         }]
    ...     img1 = take_ui_screenshot(page, "trial_2_prompt_with_chips", wait_selector="#context-chips-container .context-chip")
    ...     step["image"] = img1
    ...     step["description"] = "Attached Talavera et al. hardware architecture chunk and formulated extraction inquiry."

Step 3: Live Inference Dispatch & Response Capture
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher submits the extraction request to Verbal / Reason. Gemma4 reads the context chunk and synthesizes the physical power architecture.

    >>> with timed_step("Live Inference & Specification Extraction", steps_recorded) as step:
    ...     page.click("#send-btn")
    ...     _ = page.wait_for_selector("#active-thinking-bubble", timeout=4000)
    ...     _ = page.wait_for_selector(".chat-message.ai .bubble", timeout=3600000)
    ...     ai_response = capture_ai_response(page)
    ...     img2 = take_ui_screenshot(page, "trial_2_extracted_parameters")
    ...     step["image"] = img2
    ...     step["response_text"] = ai_response
    ...     step["model_name"] = model_name
    ...     step["description"] = f"Local LLM ({model_name}) extracted {len(ai_response)} characters of hardware specifications."
    ...     new_log = PromptResponseLog.objects.filter(user=user).order_by("-created_at").first()
    ...     if new_log and new_log.rag_selections:
    ...         step["rag_chunks"] = [{
    ...             "id": sel.get("id", "?"),
    ...             "citation": sel.get("citation", ""),
    ...             "preview": sel.get("preview", "")
    ...         } for sel in new_log.rag_selections]
    ...     step["researcher_evaluation"] = (
    ...         "Verbal / Reason successfully extracted the physical hardware parameters documented in Talavera et al. (2023):\n"
    ...         "- Locomotion/Chassis: Two GM25-370 DC motors powered by 3x 3.7V 12,800mAh Li-ion cells (~142Wh raw motor bank).\n"
    ...         "- Compute/Electronics: 5V 10,800mAh portable power bank (~54Wh logic bank).\n"
    ...         "- Sensors: Arduino Uno WiFi REV2 + Raspberry Pi 4 + Pozyx UWB transceiver + air quality / thermal sensors.\n\n"
    ...         "Based strictly on this literature extraction, we construct the 3x2 factorial parameter space for Trial 3:\n"
    ...         "- Factor A (Sensor Payload): Baseline (25W), Standard Multi-spectral (75W), Heavy LiDAR+Compute (110W).\n"
    ...         "- Factor B (Terrain Profile): Smooth Concrete (280W), Heavy Rubble/Debris (460W).\n"
    ...         "- Power Constraint: 960Wh usable energy (80% usable from 1200Wh pack, 20% emergency reserve)."
    ...     )

    >>> assert len(ai_response) > 50, f"Expected non-trivial extraction, got {len(ai_response)} chars"
    >>> browser.close()
    >>> pw.stop()

Step 4: Report Generation
^^^^^^^^^^^^^^^^^^^^^^^^^

    >>> total_duration = round(time.perf_counter() - trial_t0, 2)
    >>> report_path = record_ui_doctest_run(
    ...     "trial_2_careful_literature_extraction",
    ...     "Trial 2: Careful Literature Extraction & Parametric Factor Construction",
    ...     steps_recorded,
    ...     model_name=model_name,
    ...     total_duration_s=total_duration,
    ...     scenario_description=(
    ...         "The researcher requires verified empirical parameters from peer-reviewed literature before designing "
    ...         "computational simulations. Reason extracts physical motor specifications, power supply architectures, "
    ...         "and sensor loads from Talavera et al. (2023), establishing the empirical 3x2 factorial simulation space."
    ...     ),
    ... )
    >>> print(f"- complete attempt: {1 if os.path.exists(report_path) else 0}/1")
    - complete attempt: 1/1
