Demo UI
========

The Demo UI is a Django template with htmx-managed updates that provides a chat interface.


App achievements
----------------

* Shows "Conversations" list and displays chat for the selected
* Allows direct RAG index search for user
* Shows Blueprints for users to adopt.

App enhancement
---------------

* Support more of the setup functions from this screen (file uploads)
* Show more of the progress through blueprints as they happen, and tool products.


UI Walkthrough
--------------

1. Main Chat Interface
^^^^^^^^^^^^^^^^^^^^^^
The main chat interface allows you to interact with the active AI model.

.. image:: ../../documents/walkthroughs/screenshots/01_main_ui_empty.png
   :width: 800
   :alt: Main UI

2. Sending a Prompt
^^^^^^^^^^^^^^^^^^^
Fill out the prompt field and optionally attach documents.

.. image:: ../../documents/walkthroughs/screenshots/02_chat_filled.png
   :width: 800
   :alt: Chat Filled

3. AI Response
^^^^^^^^^^^^^^
The AI streams its response directly into the chat bubble.

.. image:: ../../documents/walkthroughs/screenshots/03_chat_response.png
   :width: 800
   :alt: Chat Response

4. Document Management
^^^^^^^^^^^^^^^^^^^^^^
Manage and upload RAG context files via the Documents tab.

.. image:: ../../documents/walkthroughs/screenshots/04_documents_view.png
   :width: 800
   :alt: Documents View

5. Grips Explorer
^^^^^^^^^^^^^^^^^
Explore the conceptual knowledge graph populated by Grips.

.. image:: ../../documents/walkthroughs/screenshots/05_grips_explorer.png
   :width: 800
   :alt: Grips Explorer


Demo UI Views
---------------

.. automodule:: demo_ui.views
   :members:
   :undoc-members:
   :show-inheritance:
