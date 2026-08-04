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

## Status
pending
