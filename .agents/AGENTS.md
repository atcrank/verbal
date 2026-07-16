---
description: Core instructions for Antigravity agents working in this repository
---

# General Rules
- Always use Python 3.13+ syntax and features.
- Prefer the conventions of the library you are using. 
- In novel code, particularly things involving AI, prefer functional patterns and composition over deep class inheritance.
- Prefer short clear functions to long ones, where practical, but use judgement.  One 30-line function might be preferable to five ten-line functions.
- Never suppress exceptions silently. Make error messages as helpful as possible.
- Use the venv at `../../py313/bin/python` to run tests, management commands, or anything in this Python environment.

# Workflow & Skill Enforcement
- At the beginning of a new feature development step, you must invoke the `/controlled-branching` skill to isolate your progress
- Take care of the Python environment - do not add packages that will cause other packages to revert
- Before writing any new code for a feature, you must first invoke the `/spec-driven-development` skill to draft a plan.
- When drafting code according to the plan, you must begin by devising an API and tests that match the specification.
- When reviewing code, always prioritize PEP 8 compliance and invoke the `/code-reviewer` skill.

# Boundaries
- Do not modify any files inside the `documents/`, `resources/`, `workspaces/`, or `sandbox` directories unless explicitly instructed.
- Do not run database migrations. The Testing framework database fixtures will use them, but you are working in a git branch so changes to the default postgres database are a side effect. 