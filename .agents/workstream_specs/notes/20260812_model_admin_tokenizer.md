# Note: Model Admin UI & System Tokenizer Improvements

## Timestamp
2026-08-12 11:57:00

## User says
"I guess it would be even nicer if time taken and problems experienced by grabbing model files were visible and managed in the llm_api admin when a LocalAIModel is defined. Also, I would like eventually to change the System tokenizer to use the LocalAIModel - currently having it as just a string field is not good for users. Maybe just the field having a multi-select sorted for small model size. Also, not all CPUs wll have a little GPU / NPU and I am worried I am going to have problems with the design I chose."

## Current context
We are updating the `start_inference.sh` script to add an auto-restart loop with backoff and crash tracking. The user noted a few ideas for future tasks while reviewing this script implementation plan.

## What needs to be done
- Enhance `LocalAIModel` Django admin UI to show time taken and errors during model file fetching/loading.
- Refactor `SystemConfiguration.system_tokenizer_id` (currently a string field) to point to `LocalAIModel` directly (e.g. multi-select sorted by model size).
- Address architectural concern: CPU-only environments without a small GPU/NPU might struggle with the current design.

## Tags
[app_label: llm_api, model: LocalAIModel, model: SystemConfiguration, feature, architecture]

## Status
pending
