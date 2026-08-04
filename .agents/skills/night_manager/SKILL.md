---
name: night_manager
description: Use this skill to responsibly perform standard daily maintenance after code changes:
- Update the app maps (PROJECT_MAP.md and [app_label]/APP_MAP.md files).
- Review and improve the design of tests on changed code.
- Update the documentation to reflect the new code.
- and more.
---

# Verbal Project Night Manager

## Core Directives

1. **Find Previous State**: Read `.agents/workstream_specs/nightworks/nightworks_log.md` to find the git hash of the last recorded run.
2. **Analyze Changes**: Use the `run_command` tool to execute `git diff <last_hash>` (or `git log` if no last hash exists) to see what has changed today. Understand what was fixed, added, or removed.
3. **Execute Skills Sequentially**: You must sequentially adopt and apply the following skills to process today's changes:
   - First, apply the `project_overview_maintainer` skill.
   - Second, apply the `app_map_maintainer` skill.
   - Third, apply the `django_test_evaluator` skill.
   - Fourth, apply the `documentation_maintainer` skill.

## Output Formats

Log findings and changes made to `.agents/workstream_specs/nightworks/nightworks_log.md`.