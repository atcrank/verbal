# Note

## Timestamp
2026-09-29T11:45:00+10:00

## User says
Are there lessons for other views in the system that also trigger unnecessary reloading? Also I don't want the polling of huggingface to be happening uncommanded; even in our deployed environment this will be a holdup and a potential cause of failures.

## Current context
During benchmarking studio profiling, we discovered that evaluating `service_registry.ai_service` on an uninitialized worker synchronously ran docker-compose container start/stop routines and made outbound HTTP HEAD/GET requests to huggingface.co to inspect tokenizers, ballooning view response times to 8.8s. Fixing this in `studio_view` to read `SystemConfiguration.get_solo()` dropped page latency to 42ms. The user wants to ensure no other views trigger unintended lazy service initialization, and specifically that uncommanded polling of Hugging Face is prevented across the entire system.

## What needs to be done
Audit and eliminate uncommanded network access to Hugging Face and unintended lazy service initialization:
1. Audit all view entry points and API endpoints that touch `service_registry.ai_service`, `nlp_service`, `rag_service`, etc. Ensure informational and read-only UI views query relational models (`SystemConfiguration`, `LocalAIModel`) rather than accessing service properties that trigger container orchestration and model loading.
2. Enforce local-only tokenizer and model resolution in `AIService` and Hugging Face utilities (`local_files_only=True`, `HF_HUB_OFFLINE=1`, offline cache flags) so Hugging Face is never queried over the network without an explicit user command (e.g., from the Model Admin download manager).
3. Add tests verifying that standard view rendering and token counting on cached assets make zero external HTTP requests.

## Tags
llm_api, architecture, performance, huggingface, networking, bug, improvement

## Status
pending
