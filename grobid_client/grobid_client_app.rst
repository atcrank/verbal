Grobid Client App
=================

This app manages deterministic PDF parsing using the "Grobid" project's Docker image, and stores Reference information
and Citation information.  Some LLM services may be used as fallback strategies if the Grobid reading doesn't find
populate basic fields.

Database models
---------------

.. automodule:: grobid_client.models
   :members:
   :undoc-members:
   :show-inheritance:

API Reference
-------------

.. automodule:: grobid_client.api
   :members:
   :undoc-members:
   :show-inheritance:

Background Tasks
----------------

.. automodule:: grobid_client.tasks
   :members:
   :undoc-members:
   :show-inheritance:
