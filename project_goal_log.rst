Goals
======

1. Ensure support (and UI affordances in the demo_ui app) is complete for the feature set including:
- branching / forking a workspace conversation with continuation.
- spawning a derivative conversation with a handover with the aid of a "Handover" Blueprint to generate a summary of a conversation and workspace for use later.
- RAG indexing and search on Conversations.
- enhanced Grobid exploitation of semantics of referencing (research required, not sure what I mean exactly)

2. Create demo_ui usage tests and documentation with screen shots, ideally gifs captured from Playwright tests of the demo_ui app, as a doctest or set of doctests.
- this may be difficult in this dev environment where the tests run in WSL on a linux with no display manager.

3.  Exercise the LoRA / Distillation functions:
- create adequate datasets (need to collect that - ideas: ProLog? Causal reasoning from PLT papers?)
- train LoRA
- use LoRA
- benchmark LoRA
- consider whether a LoRA should actually be selected at a ReasoningStep - an expert planner for a planning step, an expert coder for a coding step etc

4. Add translation path from "Skills" and "Tools" conventions used by other harnesses & templates to their metacognition equivalent forms (Blueprints / Actions - maybe this reveals that its not even the same thing?).

5. Ensure Blueprint/ReasoningStep evolution:
- uses "copy-on-write" approach. The idea is that if a user (or Nightmanager) modifies a Blueprint/ReasoningStep, they are creating a new version, not modifying the original. Traceability is maintained by reference to the parent, but is currently the responsibility of the creator of a new object.
- handles the resulting profusion of objects by tracking the current leading Blueprint / ReasoningStep patterns and techniques.
- managing among the variants by providing the Blueprints that are the most current/advanced versions.


-----------------------
Outstanding checks
-----------------------
This is things that should be feature complete and are passing the tests, but I haven't had time to get hands on. 
Most would be candidates for benchmarks but I can't rely on benchmarks alone, I need to get human hands on and evaluate them myself.

* Updated RAG/Grips context augmentation - is it good enough yet?
* We need to verify VLLM and Ollama inference work, switching between them resets properly etc
* We need to verify whether the LoRA can be applied outside Pytorch hosting (vLLM, Ollama, etc.)
* Seriously evaluate and stretch the NightManager Blueprint until it shows the start of all the capabilities. Right now I don't know if it works at all.
* (Item 3 above logically fits in this category)