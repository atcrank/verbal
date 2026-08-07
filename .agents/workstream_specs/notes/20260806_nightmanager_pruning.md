# Note

## Timestamp
2026-08-06T15:38:22+10:00

## User says
I think this OK provided its easy to separate them by user / title. It serves a similar function to the "talk" page on wikipedia  /take_a_note that we perhaps should (in future) give the NightManager a task to archive or delete things like very old PromptResponseLogs from these conversations (and similar responsibility for things like ReasoningSteps, prune out upper branching to keep variant tree depth to only three or four generations. Also things like redundant and never-retrieved Chunks that are too big or too small or too noisy, random, ill-formed to be a search result)

## Current context
Discussing the database overhead of using full Blueprints for automated background linting of ConceptNodes. User agreed to the overhead but wants automatic cleanup mechanisms.

## What needs to be done
Design and implement cleanup/archival routines in the NightManager (celery periodic tasks) to:
1. Delete very old `PromptResponseLogs`.
2. Prune `ReasoningStep` variant trees to keep max depth at 3 or 4 generations.
3. Remove redundant, ill-formed, or never-retrieved `Chunk` records (from RAG search results).

## Tags
NightManager, cleanup, pruning, Background Tasks

## Status
pending
