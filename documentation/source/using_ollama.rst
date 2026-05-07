Ollama as Local AI
===================

Using a local Ollama service is an excellent way to test your external proxy functionality without spending money on OpenAI or Anthropic credits.

Because Ollama natively provides an OpenAI-compatible API layer out of the box, your Django application won't know the difference between a local Ollama container and the actual OpenAI servers. It will successfully test your HTTP routing, your payload formatting (including the strict JSON schemas), and your response parsing.

Here is exactly how to set it up and test it through your new Django Admin architecture.

Step 1: Start Ollama and Pull a Model
-------------------------------------

If you have Ollama installed natively or running via the Docker container discussed earlier, make sure it is running. Then, pull a lightweight model to test with. (Since you like Qwen, let's use that):

.. code-block:: bash

    ollama run qwen2.5:3b

You can press ``Ctrl+D`` to exit the chat prompt once it downloads and verifies; the Ollama server runs in the background.

Step 2: Configure the External Model in Django Admin
----------------------------------------------------

Open your Django Admin (``http://localhost:8000/admin/``) and navigate to the **External AI Models** table. Add a new record:

* **Name:** Local Ollama Qwen
* **Provider:** openai (Crucial: This tells your app to use the standard OpenAI payload, which Ollama expects).
* **Api url:** ``http://127.0.0.1:11434/v1/chat/completions`` (This is Ollama's default OpenAI-compatible endpoint).
* **Api model name:** qwen2.5:3b (This must match the Ollama model tag exactly).
* **Context window:** 8192 (or whatever you prefer).

Step 3: Create a Dummy API Key
------------------------------

Even though Ollama doesn't strictly check for an API key, standard API clients often expect the header to be present. Navigate to **User API Keys** and add one:

* **User:** Select your admin user.
* **Provider:** openai (Must match the provider name you used above).
* **Api key:** ollama-dummy-key

Step 4: Route Your User's Traffic
---------------------------------

Navigate to **User Active Models** (your routing preferences table) and create/update the record for your user:

* **User:** Your admin user.
* **Active external:** Select the *Local Ollama Qwen* model you just created.
* **Use external:** Check this box!

Step 5: Test It!
----------------

Now, trigger a generation. You can do this by:

1. Going to the Vector Index Explorer and generating a summary for a chunk.
2. Going to the Regex preview tool and generating a candidate.
3. Just chatting in your standard conversation interface.

**How you'll know it's working:** If you look at the terminal running your web or worker instance, you won't see the standard HuggingFace/Outlines generation logs. Instead, you can look at your Ollama server logs. If you are running Ollama natively, you can open a terminal and run ``ollama ps``. When you trigger a request from Django, you'll see the ``qwen2.5:3b`` model spike in CPU/GPU usage as it processes the HTTP request from your proxy!

A Quick Note on Structured JSON (Outlines vs Ollama)
----------------------------------------------------

Your ``ai_service.py`` is formatting the structured output exactly to the bleeding-edge OpenAI standard:

.. code-block:: json

    "response_format": {
        "type": "json_schema",
        "json_schema": {
            "name": "json_schema",
            "schema": { ... },
            "strict": true
        }
    }

.. note::
    Ollama recently added support for this exact syntax (as of version 0.3.0+). If Ollama complains or ignores the schema and outputs raw markdown instead of strict JSON, just make sure you have updated your Ollama installation to the latest version.

Once this works with Ollama, switching to actual OpenAI or Anthropic (via an OpenAI bridge) is literally just swapping the URL to ``https://api.openai.com/v1/chat/completions``, changing the model name to ``gpt-4o-mini``, and dropping in a real API key!