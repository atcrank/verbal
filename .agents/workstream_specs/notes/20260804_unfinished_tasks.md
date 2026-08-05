# Note

## Timestamp
2026-08-04T16:30:06+10:00

## User says
There are a number of stub capabilities that have code written or proposed but without having been exercised: 
- LoRA affordances including creating training data, running appropriate dimensioned LoRA training, and installing and using the LoRA with the model it was constructed for)
- Demo_ui doctests (with screenshots - ideally animated screenshots - from Playwright)
No doubt there are others that I haven't recalled. Can we start by collecting 'the things we didn't get time to finish' and perhaps registering them with the /take_a_note skill.

## Current context
We recently concluded a large repository cleanup and test fix session. The user is now planning future work and wants to take inventory of unfinished "stub capabilities" and unimplemented features across the project.

## What needs to be done
We need to document and implement the unresolved stub capabilities:
1. **LoRA Affordances**: Implement workflows for creating training data, executing dimensioned LoRA training, and dynamically loading the trained adapters into the target LLM.
2. **Demo UI Doctests**: Set up and execute Playwright-driven doctests to automate testing of the Demo UI and capture animated screenshots of the features.
3. **LLM API Message Roles**: Resolve the TODO in `llm_api/api.py` regarding the system prompt appearing only once and subsequent messages not being typed as system prompts.
4. **Background Resources Multi-reading**: Complete the implementation and parameters for multi-reading and sub-chunking strategies noted in `background_resources/models.py`.
5. **Grips Automated Curation**: Follow up on filling GRIPS `ConceptNode` stubs (Automated Curation via background Celery tasks, referenced in `grips_app.rst` and `seed_grips_stub_filler`).

## Tags
[Feature] [LoRA, demo_ui, llm_api, background_resources, grips, doctests]

## Status
pending
