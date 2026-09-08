Tutorial 3: Interactive Conversation Branching and Grips Knowledge Graph
=======================================================================

We imagine a researcher who has the following task and resources:
An autonomous robotics research team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason interface to explore existing literature, formulate causal hypotheses, evaluate statistical and combinatory factor trade-offs, compute factorial parameters in the sandbox, and structure the domain knowledge graph.

In this third tutorial, the researcher investigates an architectural counterfactual arising from Trial 2:

1. **Problem Context**: Trial 2 demonstrated that traversing heavy rubble while powering high-draw onboard LiDAR and communications exhausts the battery in 101 minutes, violating the 120-minute mission requirement.
2. **Exploratory Hypothesis**: The researcher hypothesizes that offloading communications to lightweight, deployable ad-hoc RF breadcrumb nodes will reduce onboard power draw sufficiently to exceed the 2-hour threshold.
3. **Conversation Branching**: The researcher queries Verbal / Reason on relay trade-offs, then clicks ``Branch from here`` to fork an isolated conversation branch exploring this tactical divergence without polluting the parent thread.
4. **Knowledge Graph Integration**: The researcher navigates to the Grips Explorer, locates an unelaborated domain stub (``Breadcrumb Relay Node``), and triggers autonomous elaboration. Verbal / Reason (Gemma4) synthesizes a dense wiki entry with atomic relational claims, updating the ontology.

Execution
---------

First, we set up credentials and seed the domain ontology with an unelaborated concept node.

    >>> import os, json, time
    >>> os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    >>> from pathlib import Path
    >>> from django.contrib.auth import get_user_model
    >>> from llm_api.models import Conversation, PromptResponseLog
    >>> from grips.models import Domain, ConceptNode, KnowledgeEdge
    >>> from demo_ui.demo_ui_trials.helpers import take_ui_screenshot, capture_ai_response, record_ui_doctest_run, timed_step
    >>> from playwright.sync_api import sync_playwright

    >>> User = get_user_model()
    >>> user, _ = User.objects.get_or_create(username="robotics_researcher", defaults={"is_staff": True, "is_superuser": True})
    >>> _ = user.set_password("rescue2026")
    >>> user.save()
    >>> model_name = get_active_model_name()
    >>> trial_t0 = time.perf_counter()

Step 1: Seed Grips Domain with a Stub Concept Node
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher establishes the tactical ontology for emergency robotics, creating an unelaborated stub node for deployable breadcrumb relays.

    >>> from django.core import serializers
    >>> from background_resources.models import Document, RAGChunk
    >>> from llm_api.apps import service_registry
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

    >>> domain, _ = Domain.objects.get_or_create(
    ...     name="Firefighting Tactics",
    ...     defaults={"description": "Ontology of tactical emergency robotics and incident operations."}
    ... )
    >>> stub_node, _ = ConceptNode.objects.get_or_create(
    ...     domain=domain,
    ...     title="Breadcrumb Relay Node",
    ...     slug="breadcrumb-relay-node",
    ...     defaults={"narrative_content": "", "focus_hint": "Ad-hoc RF mesh repeaters deployed by UGVs in RF-opaque structures"}
    ... )
    >>> is_stub = not bool(stub_node.narrative_content)
    >>> print(f"Concept node initialized: '{stub_node.title}', is_stub: {is_stub}")
    Concept node initialized: 'Breadcrumb Relay Node', is_stub: True

Step 2: Live Prompt & AI Response (Parent Conversation)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher opens the Demo UI and submits a high-level architectural comparison between onboard relays and deployable breadcrumb nodes.

    >>> steps_recorded = []
    >>> pw = sync_playwright().start()
    >>> browser = pw.chromium.launch(headless=True)
    >>> page = browser.new_page(viewport={"width": 1280, "height": 800})
    >>> authenticate_page(page, user)

    >>> with timed_step("Parent Inquiry: Communication Relays vs Breadcrumbs", steps_recorded) as step:
    ...     _ = page.goto(f"{live_server_url}/demo/")
    ...     _ = page.wait_for_selector("#chat-form", timeout=6000)
    ...     prompt_text = "Compare heavy tracked UGV communication relays vs. deployable ad-hoc RF breadcrumb nodes in reinforced concrete basements."
    ...     step["researcher_thinking"] = (
    ...         "In Trial 2, heavy rubble limited robot endurance to 101 minutes with full sensor/comms payloads. "
    ...         "We must determine whether offloading communications to ad-hoc breadcrumb nodes provides sufficient "
    ...         "RF signal reliability through reinforced concrete while reducing vehicle payload power draw."
    ...     )
    ...     step["researcher_inquiry"] = prompt_text
    ...     page.fill('textarea[name="user_prompt"]', prompt_text)
    ...     page.click("#send-btn")
    ...     _ = page.wait_for_selector("#active-thinking-bubble", timeout=4000)
    ...     _ = page.wait_for_selector(".chat-message.ai .bubble", timeout=3600000)
    ...     ai_response = capture_ai_response(page)
    ...     img1 = take_ui_screenshot(page, "trial_3_parent_conversation")
    ...     step["image"] = img1
    ...     step["response_text"] = ai_response
    ...     step["model_name"] = model_name
    ...     step["description"] = f"Local LLM ({model_name}) synthesized trade-off comparison between UGV relays and breadcrumb nodes."

    >>> assert len(ai_response) > 30, f"Expected non-trivial response, got {len(ai_response)} chars"

Step 3: Interactive Conversation Branching
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher clicks ``Branch from here`` directly on the assistant turn, spawning an isolated DAG branch to investigate breadcrumb deployment protocols without polluting the baseline trial.

    >>> with timed_step("Interactive Conversation Branching", steps_recorded) as step:
    ...     conv = Conversation.objects.filter(user=user).order_by("-start_time").first()
    ...     step["researcher_thinking"] = (
    ...         "We want to explore the specific operational mechanics of breadcrumb dropping intervals. "
    ...         "Rather than cluttering our primary experimental log, we fork an exploratory branch in the Reason UI."
    ...     )
    ...     page.click(".branch-btn")
    ...     _ = page.wait_for_selector(".conv-item.active", timeout=6000)
    ...     img2 = take_ui_screenshot(page, "trial_3_branched_conversation")
    ...     step["image"] = img2
    ...     branched_conv = Conversation.objects.filter(user=user).exclude(id=conv.id).order_by("-start_time").first()
    ...     branch_ok = branched_conv is not None
    ...     step["description"] = f"Forked conversation branch from AI response. Branch created: {branch_ok}."
    ...     step["researcher_evaluation"] = (
    ...         "A child conversation branch was created and set as active in the Demo UI sidebar. "
    ...         "All subsequent prompts in this branch remain isolated from the parent baseline analysis."
    ...     )

    >>> print(f"Conversation successfully forked: {branched_conv is not None}")
    Conversation successfully forked: True

Step 4: Grips Knowledge Graph Navigation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher navigates to the Grips Explorer tab and expands the ``Firefighting Tactics`` domain to locate the unelaborated ``Breadcrumb Relay Node`` stub.

    >>> with timed_step("Grips Explorer Navigation", steps_recorded) as step:
    ...     page.click('button:has-text("Grips Explorer")')
    ...     _ = page.wait_for_selector("#grips-tab.active", timeout=6000)
    ...     _ = page.wait_for_selector(".domain-section summary", timeout=6000)
    ...     page.click('.domain-section summary')
    ...     _ = page.wait_for_selector('button:has-text("Fill Stub")', timeout=6000)
    ...     img3 = take_ui_screenshot(page, "trial_3_grips_explorer")
    ...     step["image"] = img3
    ...     step["description"] = "Navigated to Grips Explorer, expanded domain hierarchy, located unelaborated stub node with 'Fill Stub' button."
    ...     step["researcher_thinking"] = (
    ...         "Our domain ontology contains an unelaborated concept for 'Breadcrumb Relay Node'. "
    ...         "We will trigger Reason's autonomous stub filler to synthesize an authoritative wiki entry "
    ...         "and extract relational claims directly into our knowledge graph."
    ...     )

Step 5: Autonomous Stub Elaboration via Gemma4
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher clicks the ``Fill Stub`` button. Verbal / Reason (Gemma4) processes the node, searches internal literature, generates an encyclopedic narrative with structured claims, and saves it to the database.

    >>> with timed_step("Autonomous Stub Elaboration via Gemma4", steps_recorded) as step:
    ...     # Click Fill Stub in the UI
    ...     page.click('button:has-text("Fill Stub")')
    ...     # Wait for HTMX button swap to complete
    ...     _ = page.wait_for_selector('span:has-text("Elaborated")', state="attached", timeout=3600000)
    ...     stub_node.refresh_from_db()
    ...     # Refresh the Grips tab to show the fully populated concept
    ...     _ = page.goto(f"{live_server_url}/demo/")
    ...     page.click('button:has-text("Grips Explorer")')
    ...     _ = page.wait_for_selector("#grips-tab.active", timeout=6000)
    ...     page.click('.domain-section summary')
    ...     _ = page.wait_for_selector(".search-card-title", timeout=6000)
    ...     img4 = take_ui_screenshot(page, "trial_3_grips_elaborated")
    ...     step["image"] = img4
    ...     step["model_name"] = model_name
    ...     step["response_text"] = stub_node.narrative_content
    ...     step["description"] = f"Gemma4 generated {len(stub_node.narrative_content)} characters for '{stub_node.title}' with {len(stub_node.structured_claims)} claims."
    ...     step["researcher_evaluation"] = (
    ...         f"The '{stub_node.title}' stub was successfully elaborated by Gemma4 into an authoritative wiki article. "
    ...         f"The model extracted {len(stub_node.structured_claims)} structured relational claims connecting breadcrumb "
    ...         "nodes to our tactical robotics ontology."
    ...     )

    >>> print(f"Stub node elaborated: {bool(stub_node.narrative_content)}")
    Stub node elaborated: True

    >>> browser.close()
    >>> pw.stop()

Step 6: Report Generation
^^^^^^^^^^^^^^^^^^^^^^^^^

    >>> total_duration = round(time.perf_counter() - trial_t0, 2)
    >>> report_path = record_ui_doctest_run(
    ...     "trial_3_branching_and_grips_expansion",
    ...     "Trial 3: Conversation Branching & Grips Knowledge Graph",
    ...     steps_recorded,
    ...     model_name=model_name,
    ...     total_duration_s=total_duration,
    ... )
    >>> print(f"- complete attempt: {1 if os.path.exists(report_path) else 0}/1")
    - complete attempt: 1/1
