# Note

## Timestamp
2026-07-31 15:51:46

## User says
We need to check the janitor function that cleans up workspaces - I see a lot of reporting that folders were deleted but there are still a lot of folders. It would be a nice improvement if the doctrials re-used workspaces but empty ones.

## Current context
Reviewing the `8. proactive_nightmanager_report.rst` doctest results. The user noticed the janitor logic claims success but leaves folders behind. The user was also looking at the `demo_ui_maintainer` skill.

## What needs to be done
Investigate the workspace janitor logic. Ensure it properly deletes folders or, as an improvement, allows doctrials to re-use empty workspaces instead of creating new ones and abandoning old ones. This is an improvement/coding task.

## Tags
workspace_janitor, doctrials, bug, improvement

## Resolution
Completed in branch `feature/workspace-organization-and-reuse`:
1. Subdivided `workspaces/` root into dedicated namespaces: `workspaces/conversations/<uuid>` (interactive user conversations), `workspaces/doctests/metacognition` (reusable doctest workspace), `workspaces/tests/` (unit test scratch spaces), while preserving `agent_scripts` and `grips_okf`.
2. Coerced doctests and trial runs (`username in ('test_user', 'rag_test_user')` or trial titles) to deterministically reuse the same Conversation and workspace folder (`workspaces/doctests/metacognition`), cleaning prior test artifacts before each run.
3. Updated `actions.py` to pass relative workspace paths (`os.path.relpath(target_path, settings.WORKSPACE_ROOT)`) so arbitrary nested workspace directories resolve correctly in Docker sandbox container (`/workspace`).
4. Hardened `system_janitor` in `metacognition/meta_tools.py` to identify and prune orphaned conversation workspaces (UUID directories with no matching database row) and git-only workspaces with zero user files, while skipping `.git` directory internals during traversal so repository metadata in active workspaces is never corrupted.
5. All 31 existing legacy orphaned workspaces successfully purged. Verified with comprehensive unit test suites in `llm_api` and `metacognition` plus live doctest execution.

## Status
completed
