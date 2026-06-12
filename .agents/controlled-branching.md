---
name: controlled-branching
description: Safely develop new features by isolating agent work on a dedicated git branch with mandatory testing.
---
When this skill is invoked:
1. Generate a descriptive branch name based on the task and execute `git checkout -b <branch-name>`.
2. Invoke the `/spec-driven-development` skill to draft an implementation plan.
3. Write the code using Python 3.13+ and functional patterns. Never suppress exceptions silently.
4. Strictly avoid modifying the `documents/`, `resources/`, `workspaces/` and `sandbox/` directories.
5. Develop comprehensive Django Test Cases in `tests.py` and / or `pytest` suites for any changes in app code.
6. Add `doctest` demos in the docstrings of new functions and modules to demonstrate usage.
7. Execute `python manage.py test [app_label]` and/or `pytest` and `python -m doctest` locally, and iteratively:
   - consider the reason for the failure: environment? code? old or invalid test cases? 
   - fix failing application code.
   - fix tests that are not testing the current code against the specification
   - consider the scale of environment change and make updates that do not break: 
     - the Python environment
     - the Docker-compose hosting system
     - the 'main' development database which keeps in step with the 'main' git branch. Generate migrations but only use TestCase and the Test database.
8. Conclude by running the `/code-reviewer` skill to ensure PEP 8 compliance before handing the branch back for user review.