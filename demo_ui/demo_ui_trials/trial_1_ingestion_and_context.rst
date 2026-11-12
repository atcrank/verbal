Tutorial 1: Document Ingestion and Context-Grounded Reasoning
============================================================

We imagine a researcher who has the following task and resources:
An autonomous robotics research team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason interface to explore existing literature, formulate causal hypotheses, evaluate statistical and combinatory factor trade-offs, compute factorial parameters in the sandbox, and structure the domain knowledge graph.

In this first tutorial, the researcher:

1. **Ingests Academic Literature**: Loads the empirical research corpus on firefighting robotics (thermal sensor penetration, LiDAR attenuation, and mobility constraints) into the pgvector store.
2. **Formulates the Perception Hypothesis**: Identifies the critical causal trade-off between 3D LiDAR point clouds and long-wave infrared (LWIR) thermography in aerosolized smoke, attaching a specific literature context chip in the Reason interface.
3. **Engages Verbal / Reason**: Submits the inquiry to the active local model, receiving a context-grounded synthesis that maps out experimental factors for the robot trial.

Execution
---------

First, we set up credentials and initialize the test environment.

    >>> import os, time
    >>> os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    >>> from pathlib import Path
    >>> from django.core import serializers
    >>> from django.contrib.auth import get_user_model
    >>> from background_resources.models import Document, RAGChunk
    >>> from llm_api.apps import service_registry
    >>> from llm_api.models import PromptResponseLog
    >>> from demo_ui.demo_ui_trials.helpers import take_ui_screenshot, capture_ai_response, record_ui_doctest_run, timed_step
    >>> from playwright.sync_api import sync_playwright

    >>> User = get_user_model()
    >>> user, _ = User.objects.get_or_create(username="robotics_researcher", defaults={"is_staff": True, "is_superuser": True})
    >>> _ = user.set_password("rescue2026")
    >>> user.save()
    >>> model_name = get_active_model_name()
    >>> trial_t0 = time.perf_counter()

Step 1: Literature Ingestion & Semantic Vector Indexing
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher loads the pre-extracted scientific research corpus from the firefighting robotics fixture and triggers pgvector indexing to make literature chunks retrievable.

    >>> steps_recorded = []
    >>> with timed_step("Literature Ingestion & Vector Indexing", steps_recorded) as step:
    ...     step["researcher_thinking"] = (
    ...         "Before designing our field intervention trial, we need an authoritative knowledge base "
    ...         "grounded in empirical robotics literature. We load and index the firefighting robotics "
    ...         "literature fixture to provide factual anchors for sensor capabilities and mobility constraints."
    ...     )
    ...     fixture_path = Path("demo_ui/demo_ui_trials/fixtures/firefighting_chunks.json").resolve()
    ...     if fixture_path.exists():
    ...         with open(fixture_path, "r", encoding="utf-8") as f:
    ...             for obj in serializers.deserialize("json", f.read()):
    ...                 try:
    ...                     obj.save()
    ...                 except Exception:
    ...                     pass
    ...     rag_service = service_registry.rag_service
    ...     if rag_service:
    ...         _ = rag_service.index_unindexed_chunks()
    ...     total_chunks = RAGChunk.objects.count()
    ...     total_docs = Document.objects.count()
    ...     step["description"] = f"Loaded {total_chunks} semantic chunks from {total_docs} research papers via fixture and indexed into pgvector store."
    ...     step["researcher_evaluation"] = (
    ...         f"Vector database populated with {total_chunks} semantic chunks across {total_docs} research papers. "
    ...         "The literature corpus is ready for semantic retrieval in the Reason UI."
    ...     )
    >>> print(f"Corpus indexed into vector store: {RAGChunk.objects.count() > 0}")
    Corpus indexed into vector store: True

Step 2: Browser Navigation & Context Chip Formulation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher navigates to the Reason Demo UI, reviews the literature library, attaches a relevant RAG context chip into the prompt area, and types their domain perception inquiry.

    >>> pw = sync_playwright().start()
    >>> browser = pw.chromium.launch(headless=True)
    >>> page = browser.new_page(viewport={"width": 1280, "height": 800})
    >>> authenticate_page(page, user)

    >>> with timed_step("Context Chip Formulation & Prompt Submission", steps_recorded) as step:
    ...     _ = page.goto(f"{live_server_url}/demo/")
    ...     _ = page.wait_for_selector("#uploaded-docs-list", timeout=6000)
    ...     chunk = (
    ...         RAGChunk.objects.filter(text_content__icontains="thermal imaging camera").first()
    ...         or RAGChunk.objects.filter(text_content__icontains="Lidar, IMU, and encoder").first()
    ...         or RAGChunk.objects.filter(text_content__icontains="Lidar").filter(text_content__icontains="smoke").exclude(text_content__icontains="have become increasingly common").first()
    ...         or RAGChunk.objects.filter(text_content__icontains="smoke").first()
    ...         or RAGChunk.objects.first()
    ...     )
    ...     prompt_text = (
    ...         "When evaluating sensor payloads for autonomous ground robots in multi-story building fires, "
    ...         "what are the primary perception and localization trade-offs between 3D LiDAR point clouds "
    ...         "and long-wave infrared (LWIR) thermography in dense, aerosolized smoke?"
    ...     )
    ...     step["researcher_thinking"] = (
    ...         "We need to clearly isolate the causal factors governing sensor failure in structural fires. "
    ...         "LiDAR provides metric geometry for SLAM but suffers backscatter in dense aerosols. "
    ...         "LWIR penetrates obscurants but lacks metric depth. We attach the literature chunk and ask Reason "
    ...         "to synthesize the exact trade-off boundaries."
    ...     )
    ...     step["researcher_inquiry"] = prompt_text
    ...     page.fill('textarea[name="user_prompt"]', prompt_text)
    ...     if chunk:
    ...         citation = chunk.get_citation()
    ...         _ = page.evaluate("""(data) => {
    ...             includedContexts.push(data);
    ...             updateContextInput();
    ...         }""", {
    ...             "model": "RAGChunk",
    ...             "id": str(chunk.chunk_id),
    ...             "preview": citation,
    ...             "content": chunk.text_content
    ...         })
    ...     img1 = take_ui_screenshot(page, "trial_1_prompt_with_chips", wait_selector="#context-chips-container .context-chip")
    ...     step["image"] = img1
    ...     step["rag_chunks"] = [{"id": chunk.chunk_id, "citation": chunk.get_citation(), "preview": chunk.text_content[:200]}] if chunk else []
    ...     step["description"] = "Attached literature context chip and formulated sensor trade-off inquiry."

Step 3: Live Inference Dispatch & Response Capture
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher submits the inquiry to Verbal / Reason. The active local model analyzes the literature context and synthesizes perception trade-offs.

    >>> with timed_step("Live Inference & Response Capture", steps_recorded) as step:
    ...     page.click("#send-btn")
    ...     _ = page.wait_for_selector("#active-thinking-bubble", timeout=4000)
    ...     img2 = take_ui_screenshot(page, "trial_1_active_thinking")
    ...     # Allow ample time for local GPU inference
    ...     _ = page.wait_for_selector(".chat-message.ai .bubble", timeout=3600000)
    ...     ai_response = capture_ai_response(page)
    ...     img3 = take_ui_screenshot(page, "trial_1_grounded_response")
    ...     step["image"] = img3
    ...     step["response_text"] = ai_response
    ...     step["model_name"] = model_name
    ...     step["description"] = f"Local LLM ({model_name}) generated {len(ai_response)} characters of context-grounded synthesis."
    ...     new_log = PromptResponseLog.objects.filter(user=user).order_by("-created_at").first()
    ...     if new_log and new_log.rag_selections:
    ...         step["rag_chunks"] = [{"id": sel.get("id", "?"), "citation": sel.get("citation", ""), "preview": sel.get("preview", "")} for sel in new_log.rag_selections]
    ...     low_resp = ai_response.lower()
    ...     discusses_lidar = "lidar" in low_resp
    ...     discusses_thermal = "thermal" in low_resp or "lwir" in low_resp or "infrared" in low_resp
    ...     discusses_smoke = "smoke" in low_resp or "aerosol" in low_resp or "scattering" in low_resp
    ...     step["researcher_evaluation"] = (
    ...         f"Observed live synthesis ({len(ai_response)} chars): "
    ...         f"addresses LiDAR={discusses_lidar}, thermal/LWIR={discusses_thermal}, smoke/obscurants={discusses_smoke}. "
    ...         "(Note: quantitative power/endurance modeling factors are constructed separately in Trials 2-3 rather than assumed here.)"
    ...     )

    >>> assert len(ai_response) > 40, f"Expected non-trivial AI response, got {len(ai_response)} chars!"
    >>> browser.close()
    >>> pw.stop()

Step 4: Report Generation
^^^^^^^^^^^^^^^^^^^^^^^^^

    >>> expected_outcome = (
    ...     "Contrast 3D LiDAR metric SLAM/point clouds (degraded by smoke scattering/backscatter) "
    ...     "against LWIR/thermal imaging (penetrates smoke/obscurants without direct metric depth) "
    ...     "based accurately on the attached literature context."
    ... )
    >>> checks = [
    ...     {"label": "covers 3D LiDAR characteristics", "passed": discusses_lidar, "detail": ""},
    ...     {"label": "covers thermal / LWIR imaging", "passed": discusses_thermal, "detail": ""},
    ...     {"label": "addresses smoke/aerosol attenuation trade-offs", "passed": discusses_smoke, "detail": ""},
    ... ]
    >>> total_duration = round(time.perf_counter() - trial_t0, 2)
    >>> report_path = record_ui_doctest_run(
    ...     "trial_1_ingestion_and_context",
    ...     "Trial 1: Document Ingestion and Context-Grounded Reasoning",
    ...     steps_recorded,
    ...     model_name=model_name,
    ...     total_duration_s=total_duration,
    ...     expected_outcome=expected_outcome,
    ...     checks=checks,
    ... )
    >>> print(f"- complete attempt: {1 if os.path.exists(report_path) else 0}/1")
    - complete attempt: 1/1
