---
name: django_test_evaluator
description: Use this skill when asked to evaluate and improve the *test* coverage of the codebase.
---

# Evaluate test coverage and improve the tests in a targeted way to improve the effective coverage without adding noise.

## Core Directives

1. **Intended Scope**: This skill is concerned with Django test cases in each app's `tests.py` files (or `tests/` directories). It is *NOT* concerned with pytest or doctests.
2. **Measure First**: Before proposing or implementing anything, you MUST use the `run_command` tool to execute the test suite for the relevant app (e.g., `../../py312/bin/python manage.py test [app_label]`). Do not guess coverage; read the actual test output.
3. **Review the test coverage**: Consider the existing test cases and test conditions. Do they really test what they are supposed to be testing? Are there edge cases or real-world scenarios that are completely ignored? 
4. **Mandatory Tool Handler Contract Tests**: Every `ToolDefinition` registered in `metacognition/seed.py` or defined across apps (e.g. `metacognition.actions.*`, `metacognition.meta_tools.*`) MUST have explicit unit tests verifying:
    * All imports in the handler resolve cleanly in the production environment (no defunct or hallucinated app names).
    * Valid input parameters execute successfully and mutate the expected database entities.
    * Missing or invalid parameters return well-formed error messages without unhandled exceptions.
5. **State-Change Invariant Testing (Side-Effect Verification)**:
    * Tests for action-oriented tasks, blueprints, and tools MUST assert pre- and post-database state (e.g. `ConceptNode.objects.filter(...).count()`, `KnowledgeEdge.objects.exists()`), never relying solely on return status codes or `route_to == "SUCCESS"`.
    * Never assume that an LLM evaluator reporting `passed=True` means an underlying tool succeeded. Tests must assert actual database mutations.
6. **Full Task Execution over Import Smoke Tests**:
    * Merely importing a background task function (e.g. `from grips.tasks import task_digest_corpus_level_1`) into a test file only verifies syntax; it does not test execution.
    * Tasks must have integration tests executing the full function body with real or test fixture models, covering all branching paths (e.g. Grobid extraction vs. fallback chunking).
7. **Ensure test output capture**: Tests should write to the `test_results/` folder some appropriate output for models to verify successful running and capture failure conditions including stack traces and warnings. 
8. **Identify weak areas & Propose**: Identify the areas of the codebase that have low test coverage and propose specific improvements.
9. **Implement & Iterate**: Implement the improvements to the test coverage. **CRITICAL**: You must iteratively run the tests using `run_command` to verify your new tests actually pass. Do not blindly assume they work. If they fail:
    * Read the traceback and determine if this failure is valid and has discovered a bug or defect that must be fixed, or if it is a defect of the test data, test procedure, test environment etc that can be corrected within your scope. 
    * If the failure is a valid discovery, it is not your job to fix the bug, but you should note it and suggest that the appropriate skill takes it on. Do not make tests pass by relaxing criteria below the threshold of testing the real-world application in a way that is going to be valid long-term.
    * If the failure is not valid, iteratively amend the test design and run it until the test is valid.  

## Output Formats

Log findings and changes made to `.agents/workstream_specs/nightworks/test_evaluator_log.md`.

