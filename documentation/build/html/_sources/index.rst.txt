.. reason documentation master file

reason: Computational Study Design Assistant
============================================

Welcome to the documentation for **reason**, Django handles for gripping your Large Language Models.

**reason** is what is presently known as a "harness", a set of tools to help a human interact with and make use of Large Language Models. The motivating example is support for study designers in documenting, organizing, and expanding research questions, their scopes, constraints, and the underlying causal or structural relationships among factors. It aims to develop standard Retrieval-Augmented Generation (RAG) into a structured knowledge graph and agentic reasoning framework. This is used by users and the model's capabilities to understand, underpin and handle the domains of interest and the skilful action to support research design.

.. toctree::
   :maxdepth: 2
   :caption: Application Modules:

   llm_api_app
   background_resources_app
   benchmarking_app
   grips_app
   metacognition_app
   work_organisation_app
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



Indices and tables
==================

* :ref:`genindex`
* :ref:`modindex`
* :ref:`search`
