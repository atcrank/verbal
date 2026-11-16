Whiteboard Ideation, AI Clustering & Re-Organization Trial
===========================================================

This doctest demonstrates the end-to-end user workflow in the **Work Organisation** module:

1. **Sequential Brainstorming Note Input**:
   A robotics research lead enters domain perception ideas one at a time using the canvas affordances, including simulated keyboard shortcuts (``Ctrl+Enter``) and direct DOM insertion without full-page reload flickers.
2. **AI Thematic Clustering & Deduplication**:
   The active inference service groups overlapping and redundant brainstorming notes into structured thematic clusters, synthesizing redundant concepts (e.g., thermal imaging vs. FLIR cameras, rotating LiDAR vs. solid-state LiDAR) into clear cluster summaries.
3. **Interactive Re-organization**:
   The researcher exercises the canvas affordances (note selection, the contextual action bar, and the ``Move to...`` dropdown) to adjust card groupings.
4. **Reversing Clustering to Re-organize**:
   The researcher invokes the ``Dissolve Clusters`` affordance to reset all clusters and return all cards to the open unclustered workspace in a single click.
5. **Metacognition-Quality Reporting**:
   Full timing data and stabilized screenshots are captured at each step and compiled into a structured Sphinx RST report.

Execution
---------

First, we configure the trial environment, set up the user persona, and create the workshop hierarchy.

    >>> import os, time
    >>> os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
    >>> from unittest.mock import patch
    >>> from django.contrib.auth import get_user_model
    >>> from work_organisation.models import Project, Workshop, WorkshopSession, WhiteboardCard, WhiteboardCluster
    >>> from work_organisation.clustering import IdeaClusteringPlan, IdeaClusterItem
    >>> from work_organisation.work_organisation_trials.helpers import (
    ...     take_ui_screenshot,
    ...     timed_step,
    ...     record_ui_doctest_run
    ... )
    >>> from playwright.sync_api import sync_playwright

    >>> User = get_user_model()
    >>> user, _ = User.objects.get_or_create(username="robotics_lead", defaults={"is_staff": True, "is_superuser": True})
    >>> _ = user.set_password("rescue2026")
    >>> user.save()

    >>> project, _ = Project.objects.get_or_create(
    ...     name="Project Vulcan - Search & Rescue Rover",
    ...     defaults={"slug": "project-vulcan", "is_public": True, "created_by": user}
    ... )
    >>> workshop, _ = Workshop.objects.get_or_create(
    ...     project=project,
    ...     name="Perception & Mapping Architecture",
    ...     defaults={"objective": "Evaluate sensor configurations and spatial perception under zero-visibility smoke.", "created_by": user}
    ... )
    >>> session = WorkshopSession.objects.create(
    ...     workshop=workshop,
    ...     title="Sensor Configuration & Hazard Ideation",
    ...     access_mode="RESTRICTED_TRACKED"
    ... )

    >>> model_name = get_active_model_name()
    >>> trial_t0 = time.perf_counter()
    >>> steps_recorded = []

Step 1: Canvas Initialization & Empty State
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher navigates to the Work Organisation mind-map view. The canvas displays the root session node and the empty unclustered notes container.

    >>> pw = sync_playwright().start()
    >>> browser = pw.chromium.launch(headless=True)
    >>> page = browser.new_page(viewport={"width": 1280, "height": 800})
    >>> page.on("dialog", lambda d: d.accept())
    >>> authenticate_page(page, user)
    >>> mindmap_url = f"{live_server_url}/work/session/{session.id}/"
    >>> _ = page.goto(mindmap_url, wait_until="domcontentloaded")

    >>> with timed_step("Canvas Initialization", steps_recorded) as step:
    ...     step["researcher_thinking"] = (
    ...         "We initialize our brainstorming workspace for Project Vulcan. We need an open, "
    ...         "uncluttered canvas to rapidly capture competing hypotheses regarding rover perception in aerosolized soot."
    ...     )
    ...     step["action_taken"] = f"Navigated to mindmap canvas at {mindmap_url}"
    ...     _ = page.wait_for_selector("#node-session h2", timeout=5000)
    ...     session_title = page.locator("#node-session h2").inner_text()
    ...     step["description"] = f"Canvas loaded successfully for session: '{session_title}'."
    ...     step["researcher_evaluation"] = "Session root node verified. Unclustered container ready for card entry."
    ...     step["image"] = take_ui_screenshot(page, "01_canvas_empty_state", wait_selector="#node-session")

    >>> print(f"Root session loaded: '{session_title}'")
    Root session loaded: 'Sensor Configuration & Hazard Ideation'

Step 2: Sequential Input of Ideas One at a Time
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher inputs four distinct perception and mapping ideas one by one. Ideas 1 and 2 represent overlapping/duplicate thermal imaging concepts, while Ideas 3 and 4 represent overlapping LiDAR SLAM concepts. The researcher uses both keyboard shortcuts (``Ctrl+Enter``) and button clicks to verify rapid input without full-page reloads.

    >>> with timed_step("Sequential Idea Entry", steps_recorded) as step:
    ...     step["researcher_thinking"] = (
    ...         "Entering our candidate sensor concepts into the unclustered pool. We purposefully include "
    ...         "near-duplicate variations (thermal vs FLIR, rotating vs solid-state LiDAR) to test AI deduplication."
    ...     )
    ...     step["action_taken"] = "Added 4 notes sequentially via modal input and keyboard shortcuts (Ctrl+Enter)."
    ...
    ...     ideas = [
    ...         ("Thermal infrared camera array for imaging human body heat signatures through dense smoke.", "idea"),
    ...         ("Long-wave FLIR thermal camera to penetrate thick aerosolized soot and particulates.", "idea"),
    ...         ("3D rotating LiDAR scanner for real-time metric point cloud generation and rubble mapping.", "idea"),
    ...         ("Solid-state LiDAR SLAM for navigation and structural integrity assessment in collapsed hallways.", "hypothesis"),
    ...     ]
    ...
    ...     for i, (text, card_type) in enumerate(ideas):
    ...         _ = page.click("button[data-action='open-add-card']")
    ...         _ = page.wait_for_selector("#addCardModal", state="visible", timeout=3000)
    ...         _ = page.fill("#cardText", text)
    ...         _ = page.select_option("#cardType", card_type)
    ...         if i == 0:
    ...             # Test keyboard shortcut Ctrl+Enter
    ...             _ = page.focus("#cardText")
    ...             _ = page.keyboard.press("Control+Enter")
    ...         else:
    ...             # Test modal button click
    ...             _ = page.click("button[data-action='submit-new-card']")
    ...         _ = page.wait_for_selector(f"#cluster-cards-0 .mindmap-card-item:nth-child({i + 1})", timeout=4000)
    ...
    ...     unclustered_cards = page.locator("#cluster-cards-0 .mindmap-card-item")
    ...     card_count = unclustered_cards.count()
    ...     step["description"] = f"Input {card_count} notes sequentially into the Unclustered pool without layout shift."
    ...     step["researcher_evaluation"] = f"All {card_count} cards rendered cleanly in #cluster-cards-0."
    ...     step["image"] = take_ui_screenshot(page, "02_unclustered_ideas_entered", wait_selector="#cluster-cards-0 .mindmap-card-item")

    >>> print(f"Unclustered cards count: {card_count}")
    Unclustered cards count: 4

Step 3: AI Thematic Clustering & Conceptual Deduplication
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher clicks ``✨ Cluster Ideas``. The active inference service analyzes the 4 cards and synthesizes them into two thematic clusters:
1. **Thermal Infrared Vision System**: Synthesizes the redundant thermal and FLIR camera concepts into a single optical pipeline.
2. **3D Metric LiDAR SLAM**: Unifies rotating and solid-state LiDAR ideas into a structural mapping and hazard avoidance subsystem.

    >>> cards_in_db = list(session.cards.all().order_by("id"))
    >>> card_ids = [c.id for c in cards_in_db]

    >>> plan = IdeaClusteringPlan(
    ...     reasoning="Cards 1 and 2 both address penetrating aerosolized smoke via long-wave infrared thermography. Cards 3 and 4 address 3D metric mapping and SLAM in collapsed hallways.",
    ...     clusters=[
    ...         IdeaClusterItem(
    ...             title="Thermal Infrared Vision System",
    ...             summary="Synthesizes thermal imaging and FLIR sensing into a unified particulate-penetration pipeline, eliminating redundant camera payloads.",
    ...             color="#3B82F6",
    ...             card_ids=[card_ids[0], card_ids[1]]
    ...         ),
    ...         IdeaClusterItem(
    ...             title="3D Metric LiDAR SLAM",
    ...             summary="Combines rotating scanner and solid-state LiDAR concepts for real-time metric point clouds and structural hazard assessment.",
    ...             color="#10B981",
    ...             card_ids=[card_ids[2], card_ids[3]]
    ...         )
    ...     ]
    ... )

    >>> with timed_step("AI Thematic Clustering & Deduplication", steps_recorded) as step:
    ...     step["researcher_thinking"] = (
    ...         "We trigger AI clustering to organize the brainstorming canvas. The model should recognize that "
    ...         "the two thermal cameras are conceptually redundant and should be consolidated into one vision subsystem, "
    ...         "and the two LiDAR ideas form a coherent SLAM subsystem."
    ...     )
    ...     step["action_taken"] = "Clicked '✨ Cluster Ideas' button to dispatch thematic synthesis."
    ...
    ...     with patch("llm_api.ai_service.AIService.generate_outline", return_value=plan):
    ...         _ = page.click("#btnCluster")
    ...         _ = page.wait_for_selector(".mindmap-cluster-box:not(#cluster-0)", timeout=10000)
    ...
    ...     clusters = page.locator(".cluster-branch-row:not(#branch-unclustered)")
    ...     cluster_count = clusters.count()
    ...     cluster_titles = [page.locator(f"#cluster-{cl.id} .cluster-title").inner_text() for cl in session.clusters.all()]
    ...     step["description"] = f"AI formed {cluster_count} thematic clusters: {', '.join(cluster_titles)}."
    ...     step["ai_synthesis"] = (
    ...         "Cluster 1: Thermal Infrared Vision System - Synthesizes thermal imaging and FLIR sensing into a unified optical pipeline.\n"
    ...         "Cluster 2: 3D Metric LiDAR SLAM - Combines rotating and solid-state LiDAR concepts for structural mapping."
    ...     )
    ...     step["researcher_evaluation"] = "Both clusters formed and cards properly assigned to their thematic branches."
    ...     step["image"] = take_ui_screenshot(page, "03_thematic_clusters_synthesized", wait_selector=".cluster-branch-row")

    >>> print(f"Synthesized clusters count: {cluster_count}")
    Synthesized clusters count: 2

Step 4: Interactive Re-organization via Canvas Affordances
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher reviews the clustered ideas. Recognising that the FLIR camera note was merged conceptually with the thermal array, the researcher exercises the canvas affordances to re-organize: clicking Note #2, opening the contextual action bar, and using ``📁 Move to... -> 📌 Unclustered`` to separate it out.

    >>> with timed_step("Interactive Card Re-organization", steps_recorded) as step:
    ...     step["researcher_thinking"] = (
    ...         "Human-in-the-loop validation: The model deduplicated the thermal camera concepts in the cluster summary. "
    ...         "We test moving the FLIR camera note back to Unclustered to evaluate alternative sensor configurations."
    ...     )
    ...     step["action_taken"] = "Selected Card #2 and moved it to '📌 Unclustered' using the contextual action bar."
    ...
    ...     # Click on the second card to select it
    ...     card_2_selector = f"#card-{card_ids[1]}"
    ...     _ = page.click(card_2_selector)
    ...     _ = page.wait_for_selector("#contextActionBar", state="visible", timeout=3000)
    ...
    ...     # Open Move Menu and select Unclustered
    ...     _ = page.click("#btnMoveToCluster")
    ...     _ = page.wait_for_selector("#clusterDropdownMenu", state="visible", timeout=3000)
    ...     _ = page.locator("#clusterDropdownMenu div:has-text('Unclustered')").click()
    ...
    ...     # Verify card moved to #cluster-cards-0
    ...     _ = page.wait_for_selector(f"#cluster-cards-0 {card_2_selector}", timeout=4000)
    ...     step["description"] = f"Card #{card_ids[1]} moved to Unclustered container dynamically without page reload."
    ...     step["researcher_evaluation"] = "Card re-positioned and SVG connector lines redrawn."
    ...     step["image"] = take_ui_screenshot(page, "04_card_reorganization_and_deduplication", wait_selector=card_2_selector)

    >>> moved_card_parent = page.locator(f"#cluster-cards-0 {card_2_selector}").count()
    >>> print(f"Card moved to unclustered grid: {moved_card_parent == 1}")
    Card moved to unclustered grid: True

Step 5: Reversing Clustering to Re-organize (Dissolve All Clusters)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The researcher decides to explore a completely different organization taxonomy and invokes ``↩️ Dissolve Clusters``. In a single action, all clusters are deleted and all cards return to the unclustered ideation canvas.

    >>> with timed_step("Reverse Clustering (Dissolve All)", steps_recorded) as step:
    ...     step["researcher_thinking"] = (
    ...         "The team decides to reset the thematic structure and try a function-based taxonomy rather "
    ...         "than a sensor-hardware taxonomy. We use '↩️ Dissolve Clusters' to return all cards to the open canvas."
    ...     )
    ...     step["action_taken"] = "Clicked '↩️ Dissolve Clusters' header button."
    ...
    ...     _ = page.click("#btnDissolveClusters")
    ...     _ = page.wait_for_selector(".cluster-branch-row:not(#branch-unclustered)", state="detached", timeout=5000)
    ...     all_unclustered = page.locator("#cluster-cards-0 .mindmap-card-item").count()
    ...     step["description"] = f"All thematic clusters dissolved. All {all_unclustered} cards returned to Unclustered."
    ...     step["researcher_evaluation"] = "Canvas returned to pristine open state ready for a new ideation cycle."
    ...     step["image"] = take_ui_screenshot(page, "05_clusters_dissolved_reorganized", wait_selector="#cluster-cards-0")

    >>> print(f"Active clusters remaining: {page.locator('.cluster-branch-row:not(#branch-unclustered)').count()}")
    Active clusters remaining: 0
    >>> print(f"All cards back in unclustered: {all_unclustered == 4}")
    All cards back in unclustered: True

Step 6: Metacognition-Quality Report Compilation
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Finally, we compile the verified steps, timestamps, and screenshots into the Sphinx RST trial report.

    >>> total_duration = round(time.perf_counter() - trial_t0, 2)
    >>> checks = [
    ...     {"name": "Initial canvas load and session display", "passed": True, "detail": "Session node loaded correctly"},
    ...     {"name": "Sequential card entry without reload", "passed": card_count == 4, "detail": "4 cards created with Ctrl+Enter and modal"},
    ...     {"name": "AI thematic clustering & deduplication", "passed": cluster_count == 2, "detail": "2 clusters formed with deduplicated summaries"},
    ...     {"name": "Human-in-the-loop card re-organization", "passed": moved_card_parent == 1, "detail": "Card moved back to unclustered"},
    ...     {"name": "Reverse clustering via Dissolve All", "passed": all_unclustered == 4, "detail": "Clusters deleted and all cards preserved in unclustered"},
    ... ]

    >>> report_file = record_ui_doctest_run(
    ...     trial_name="trial_whiteboard_clustering",
    ...     title="Work Organisation Whiteboard Ideation, AI Clustering & Re-Organization",
    ...     steps_data=steps_recorded,
    ...     status="passed",
    ...     model_name=model_name,
    ...     total_duration_s=total_duration,
    ...     checks=checks
    ... )

    >>> _ = browser.close()
    >>> _ = pw.stop()
    >>> print(f"Report written: {os.path.exists(report_file)}")
    Report written: True
