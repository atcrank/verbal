# Note: Multi-Model Comprehensive Review Guidelines

## Timestamp
2026-09-21T10:25:00+10:00

## User says
"Good morning - I would like to convene a multi-model review of this entire project, evaluating:
1. whether and where we offer advantages or where we are beaten by existing projects to a user-base of working scientists and researchers who use detailed physics-based simulation of complex sociotechnical systems. 
2. whether our code quality and integration and performance is where it should be, and where it could be improved.
3. whether our test coverage is adequate, whether tests are designed to effectively test performance that matters or whether they are just making up the numbers with tests that pass.
4. Whether our documentation and doctest trial approaches are very good - really clear, offering all the advice and guidance that might be required.
5. Suggestions for growth paths, easy improvements and identification of growth blockers.
Please draft a set of guidelines that will introduce the whole project, instruct about the task including encouragement to check every app, to ask questions, and request feedback. The reviewing agents should then each write their own report - perhaps as a 'Workstream Spec Candidate' in the workstream specs."

## Current context
Reason has transitioned into an empirical research co-pilot with local LLM serving, native `verbal_tasks`, LangGraph cognitive blueprints, Docker sandboxing, and Playwright doctest trials. A multi-model review is convened to evaluate the system against scientific simulation standards and identify growth paths and blockers.

## What needs to be done
Draft and maintain the master protocol and guidelines for reviewing agents in `.agents/workstream_specs/MULTI_MODEL_REVIEW_GUIDELINES.md`. Instruct participating models to audit all 12 apps, ask clarifying questions, evaluate against the 5 pillars, and output individual Workstream Spec Candidates in `.agents/workstream_specs/candidate_review_<agent_model_name>.md`.

## Tags
review, multi_model, architecture, testing_rigor, documentation, doctests, sociotechnical_simulation, physics, enduring

## Status
in progress
