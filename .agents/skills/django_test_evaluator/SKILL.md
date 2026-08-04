---
name: django_test_evaluator
description: Use this skill when asked to evaluate and improve the *test* coverage of the codebase.
---

# Evaluate test coverage and improve the tests in a targeted way to improve the effective coverage without adding noise.

## Core Directives

1. **Intended Scope**: This skill is concerned with Django test cases in each app's `tests.py` files (or `tests/` directories). It is *NOT* concerned with pytest or doctests.
2. **Measure First**: Before proposing or implementing anything, you MUST use the `run_command` tool to execute the test suite for the relevant app (e.g., `../../py313/bin/python manage.py test [app_label]`). Do not guess coverage; read the actual test output.
3. **Review the test coverage**: Consider the existing test cases and test conditions. Do they really test what they are supposed to be testing? Are there edge cases or real-world scenarios that are completely ignored? 
4. **Ensure test output capture**: Tests should write to the `test_results/` folder some appropriate output for models to verify successful running and capture failure conditions including stack traces and warnings. 
5. **Identify weak areas & Propose**: Identify the areas of the codebase that have low test coverage and propose specific improvements.
6. **Implement & Iterate**: Implement the improvements to the test coverage. **CRITICAL**: You must iteratively run the tests using `run_command` to verify your new tests actually pass. Do not blindly assume they work. If they fail:
    * Read the traceback and determine if this failure is valid and has discovered a bug or defect that must be fixed, or if it is a defect of the test data, test procedure, test environment etc that can be corrected within your scope. 
    * If the failure is a valid discovery, it is not your job to fix the bug, but you should note it and suggest that the appropriate skill takes it on. Do not make tests pass by relaxing criteria below the threshold of testing the real-world application in a way that is going to be valid long-term.
    * If the failure is not valid, iteratively amend the test design and run it until the test is valid.  

## Output Formats

Log findings and changes made to `.agents/workstream_specs/nightworks/test_evaluator_log.md`.

