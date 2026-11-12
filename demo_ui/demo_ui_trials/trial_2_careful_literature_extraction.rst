Tutorial 2: Careful Literature Extraction and Parametric Factor Construction
===========================================================================

We imagine a researcher who has the following task and resources:
An autonomous robotics research team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason interface to explore existing literature, formulate causal hypotheses, evaluate statistical and combinatory factor trade-offs, compute factorial parameters in the sandbox, and structure the domain knowledge graph.

In this second tutorial, the researcher bridges the gap between high-level qualitative literature and concrete numerical simulation:

1. **Epistemological Lineage**: Before any mathematical simulations are executed, every physical parameter (battery capacities, motor voltage, sensor draw) must be explicitly extracted from peer-reviewed literature.
2. **Context Attachment**: The researcher locates and attaches the primary hardware architecture section from Talavera et al. (2023) (*An autonomous ground robot to support firefighters' interventions in indoor emergencies*).
3. **Targeted Extraction Inquiry**: The researcher submits an extraction prompt to Verbal / Reason demanding exact physical specifications for chassis motors, compute boards, telemetry, and power supplies.
4. **Parametric Factor Construction**: Grounded in the extracted physical architecture (dual battery split and modular compute/sensor levels), the researcher explicitly defines scaled heavy-UGV scenario assumptions for Trial 3's $3 \times 2$ factorial matrix.

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

Step 1: Inspect and Pin Salient Hardware Architecture Literature
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
We seed a compact salient hardware excerpt extracted cleanly from Talavera et al. (2023) Table 2 / Section 3 (`Level 0` and `Level 1` specs) so small models are not overwhelmed by a 1,100-token full-section wall of text.

    >>> _ = RAGChunk.objects.filter(chunk_id="talavera-level0-hardware").delete()
    >>> hardware_chunk = RAGChunk.objects.create(
    ...     chunk_id="talavera-level0-hardware",
    ...     text_content=(
    ...         "Level 0 powers the robot and executes its movements: two tracked belts guided by six wheels, "
    ...         "two DC motors (GM25-370), a portable 5 V / 10,800 mAh lithium power bank (~54 Wh) for sensing and "
    ...         "control electronics, and three rechargeable 3.7 V / 12,800 mAh lithium-ion cells (~142 Wh) for the motors. "
    ...         "Level 1 handles control and sensors via an SKU DRI0002 motor driver, Arduino UNO WiFi REV2, "
    ...         "Raspberry Pi 4, Pozyx UWB localization module, and environmental/air-quality sensors."
    ...     ),
    ...     metadata={
    ...         "authors": "Talavera et al.",
    ...         "year": "2023",
    ...         "section_title": "Architecture Levels 0-1 (Compact Excerpt)",
    ...         "filename": "documents/Journal_of_Field_Robotics_-_2023_-_Talavera_-_An_autonomous_ground_robot_to_su_hjXU4xB.pdf",
    ...     },
    ... )
    >>> if rag_service and getattr(rag_service, "embeddings", None):
    ...     vec = rag_service.embeddings.embed_documents([hardware_chunk.text_content])[0]
    ...     hardware_chunk.embedding = vec
    ...     hardware_chunk.in_vector_index = True
    ...     hardware_chunk.save(update_fields=["embedding", "in_vector_index"])
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
The researcher submits the extraction request to Verbal / Reason. The active model reads the compact context excerpt and synthesizes the physical power architecture.

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
    ...     low_resp = ai_response.lower()
    ...     has_motors = "gm25-370" in low_resp
    ...     has_motor_cells = "12,800" in ai_response or "12.800" in ai_response or "142" in ai_response
    ...     has_logic_bank = "10,800" in ai_response or "10.800" in ai_response or "54" in ai_response
    ...     has_mcus = "arduino" in low_resp and "raspberry pi" in low_resp
    ...     step["researcher_evaluation"] = (
    ...         f"Observed extraction checks — GM25-370 motors={has_motors}, motor cells (3.7V/12,800mAh)={has_motor_cells}, "
    ...         f"logic bank (5V/10,800mAh)={has_logic_bank}, microcontrollers (Arduino/RPi4)={has_mcus}.\n\n"
    ...         "Researcher Design Framing (Scenario Scaling for Trial 3, distinct from Talavera's micro-UGV cells):\n"
    ...         "Guided by Talavera's isolated power/logic split, the Researcher scales up a synthetic heavy search-and-rescue "
    ...         "factorial parameter space for Trial 3: Sensor Payloads (25W / 75W / 110W), Terrains (280W / 460W), "
    ...         "and a 1200Wh scaled battery bank (960Wh usable at 80% depth-of-discharge)."
    ...     )

    >>> assert len(ai_response) > 50, f"Expected non-trivial extraction, got {len(ai_response)} chars"
    >>> browser.close()
    >>> pw.stop()

Step 4: Report Generation
^^^^^^^^^^^^^^^^^^^^^^^^^

    >>> expected_outcome = (
    ...     "Accurately extract Talavera et al. (2023) physical components from the attached chunk: "
    ...     "two GM25-370 DC motors, 3x 3.7V 12,800mAh cells (~142Wh) for motors, 5V 10,800mAh bank (~54Wh) for electronics, "
    ...     "Arduino UNO WiFi REV2 + Raspberry Pi 4 + SKU DRI0002 + Pozyx UWB."
    ... )
    >>> checks = [
    ...     {"label": "extracted GM25-370 motors", "passed": has_motors, "detail": ""},
    ...     {"label": "extracted 3.7V 12,800mAh motor bank", "passed": has_motor_cells, "detail": ""},
    ...     {"label": "extracted 5V 10,800mAh logic bank", "passed": has_logic_bank, "detail": ""},
    ...     {"label": "extracted Arduino + Raspberry Pi 4", "passed": has_mcus, "detail": ""},
    ... ]
    >>> total_duration = round(time.perf_counter() - trial_t0, 2)
    >>> report_path = record_ui_doctest_run(
    ...     "trial_2_careful_literature_extraction",
    ...     "Trial 2: Careful Literature Extraction & Parametric Factor Construction",
    ...     steps_recorded,
    ...     model_name=model_name,
    ...     total_duration_s=total_duration,
    ...     scenario_description=(
    ...         "The researcher extracts verified hardware specifications from Talavera et al. (2023) "
    ...         "using Reason, separating extracted source facts from the Researcher's scaled 3x2 scenario inputs."
    ...     ),
    ...     expected_outcome=expected_outcome,
    ...     checks=checks,
    ... )
    >>> print(f"- complete attempt: {1 if os.path.exists(report_path) else 0}/1")
    - complete attempt: 1/1
