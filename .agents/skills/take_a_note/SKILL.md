---
name: take_a_note
description: Use this skill when asked to take a note or note an issue, idea or anything for later processing.
---

# Take a Note

## Core Directives

1. **Token Efficiency is Paramount**: Do NOT include code snippets, full database schemas, or function signatures except for clarity.
2. **Structure the Note**: Use the following format:
    1. **Timestamp**: [Timestamp]
    2. **User says**: [User's input to be noted]
    3. **Current context**: [What was happening before the user asked to take a note]
    4. **What needs to be done**: [What the user wants to do and is it a discussion? research? a cleanup task?]
    5. **Tags**: [Tags - code scope (app_label, model or function), issue type (bug, feature, improvement, or a "keep in mind" constraint, etc. )]
    6. **Status**: [Status: pending, in progress, done, enduring, archived etc. ]
3. **STRICT ISOLATION**: Your ONLY job in this skill is to document the note. Under NO circumstances should you modify application code, run migrations, or execute shell commands that could restart the server.

## Output Formats
1. Use a dedicated folder in `.agents/workstream_specs/notes` for storing the notes.
Example Note:
```markdown
# Note

## Timestamp
[Timestamp]

## User says
[User's input to be noted]

## Current context
[What was happening before the user asked to take a note]

## What needs to be done
[What the user wants to do and is it a discussion? research? a cleanup task?]

## Tags
[Tags - code scope (app_label, model or function), issue type (bug, feature, improvement, or a "keep in mind" constraint, etc. )]

## Status
[Status: pending, in progress, done, enduring, archived etc. ]
```
2. Create an entry in INDEX.md that keeps track of all the notes.

Example INDEX.md entry:
* [2026-07-31] [Feature] Fix the sub-blueprint error looping trap (Status: pending) -> [Link to note]

3. If the Note concerns a clear coding task (like "fix this bug") add it the Kanban board at `.agents/workstream_specs/TICKETS.md` as well. Only create the ticket if it doesn't exist. If there is already a TICKET line-item, update it with the details of this note. Infer the title and status from the Note and the context of the current conversation. Include a brief summary of the issue and a link to the note.  Use github markdown for tasks for each entry.

Example TICKETS.md entry:
- [ ] [Feature] Fix the sub-blueprint error looping trap (Status: pending) -> [Link to note]

