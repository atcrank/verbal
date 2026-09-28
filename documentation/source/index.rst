.. reason documentation master file

reason: Computational Study Design Assistant
============================================

Welcome to the documentation for **reason**, Django handles for gripping your Large Language Models.

**reason** is what is presently known as a "harness", a set of tools to help a human interact with and make use of Large Language Models. The motivating example is support for study designers in documenting, organizing, and expanding research questions, their scopes, constraints, and the underlying causal or structural relationships among factors. It aims to develop standard Retrieval-Augmented Generation (RAG) into a structured knowledge graph and agentic reasoning framework. This is used by users and the model's capabilities to understand, underpin and handle the domains of interest and the skilful action to support research design.

.. toctree::
   :maxdepth: 2
   :caption: Application Modules:

   llm_api_app
   verbal_tasks_app
   sandbox_manager_app
   grobid_client_app
   background_resources_app
   grips_app
   metacognition_app
   work_organisation_app
   benchmarking_app
   demo_ui_app

.. toctree::
   :maxdepth: 2
   :caption: Guides & Testing:

   using_ollama
   metacognition_trials
   demo_ui_trials
   tests


Quickstart
----------

**reason** uses a modern distributed architecture to ensure heavy AI inference does not block the web interface or background workers.

To start the full system locally:

1. **Start the Background Worker & Scheduler:**
   Start the native database-backed task worker and periodic scheduler:

   .. code-block:: bash

       python manage.py runtaskworker
       python manage.py runtaskscheduler

2. **Start the Inference Server:**
   Open a new terminal, activate your environment, and start the local GPU inference server on port 8001.

   .. code-block:: bash

       export VERBAL_ROLE=inference
       python manage.py runserver 8001

3. **Start the Web Interface:**
   Open another terminal, activate your environment, and start the lightweight Django web/admin server on port 8000.

   .. code-block:: bash

       export VERBAL_ROLE=web
       python manage.py runserver 8000

You can now navigate to ``http://localhost:8000/demo/`` or ``http://localhost:8000/admin/`` to access the system.

Interactive API self-description (OpenAPI standard) is at ``http://localhost:8000/api/docs/``


Configuration Guide
-------------------

**reason** utilizes environment variables to dictate how the current process interacts with the AI pipelines.

Role Management (``VERBAL_ROLE``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

* ``inference``: Loads massive HuggingFace models (e.g., Qwen, Gemma) directly into GPU VRAM. It acts as an internal microservice.
* ``web``: Bypasses heavy model loading. Acts as a lightweight HTTP client routing generation requests to the inference server.
* ``worker``: Used by background task workers. Identical to the ``web`` role but includes small pauses to allow the UI to interleave requests.

Inference Routing (``INFERENCE_URL``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

By default, web and worker roles route their requests to ``http://127.0.0.1:8001/api/llm``. You can override this if you decide to host the inference server on a different machine on your local network.

External Models (OpenAI, Ollama)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**reason** natively supports standard OpenAI ``/v1/chat/completions`` endpoints. To route traffic to an external provider or a containerized Ollama instance, configure an **External AI Model** in the Django Admin and assign it via the **User Active Models** table. See the :doc:`using_ollama` guide for a complete walkthrough.

Tool Governance & Restricted Run-Modes (``VERBAL_LOCKDOWN_LEVEL``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

**reason** enforces a 3-tier security governance architecture to restrict autonomous agent capabilities and prevent model code execution or data exfiltration:

* ``DEVELOPMENT``: Full tool access for AI research, including code execution and self-modification.
* ``CONTROLLED``: Regulated mode. Code execution requires user clearance (``TRUSTED`` or ``ADMIN``); self-modification is blocked.
* ``RESTRICTED``: Safe evaluation mode. Code execution and external network tools are completely disabled host-wide. Deterministic domain tools (RAG search, Grips graph updates) remain active.
* ``AIR_GAPPED`` (Aliases: ``TEXT_ONLY``, ``LOCKED``): Pure reasoning mode. All runtime model tools are blocked. The system operates strictly via multi-turn reasoning and structured output schemas. (Internal microservice plumbing between web, inference, and grobid remains intact).

To apply a restricted mode in ``.env``:

.. code-block:: bash

    VERBAL_LOCKDOWN_LEVEL=RESTRICTED
    ALLOW_MODEL_CODE_EXECUTION=False
    ALLOW_TOOL_NETWORK_ACCESS=False
    ALLOW_AGENT_SELF_MODIFICATION=False

For complete architectural details, see the :doc:`metacognition_app` documentation.




Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
