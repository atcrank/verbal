# Datastar SSE Streaming Under Granian (ASGI vs WSGI Generator Execution)

## Timestamp
2026-11-25 09:40:00 AEDT

## User says
"The only technical point that worried me is that the proposed move from SSE to async (numbered '4. Side Finding') - because SSE is used by the Datastar fontend library that we have adopted and I want to make that decision carefully."

## Current context
During the WS43 regression diagnosis, an internal Django 6.1 + Granian ASGI detail was noted alongside the Grobid and inference bugs. The note's shorthand ("convert SSE generators to async") sounded as if moving away from Datastar's Server-Sent Events (`text/event-stream`) hypermedia architecture was being proposed. Datastar SSE (`DatastarSSE.patch_elements` and `DatastarSSE.patch_signals` over `text/event-stream`) is Core Architectural Invariant 5 and is NOT changing. The actual question is strictly how the server flushes those Datastar SSE frames: under `granian --interface asgi`, passing a synchronous Python `def` generator to `StreamingHttpResponse` triggers Django's `sync_to_async(list)` fallback, which buffers frames until the generator exits instead of flushing each Datastar SSE patch immediately.

## What needs to be done
Conduct a standalone, careful architectural discussion and empirical browser check (completely separate from WS43 bug remediation) to choose between the two zero-frontend-change ways of ensuring immediate Datastar SSE frame flushing in production:
1. **Option A (`granian --interface wsgi`)**: Since `verbal_config/asgi.py` uses no WebSockets or Django Channels (all real-time UI updates use Datastar HTTP SSE), evaluate whether running Granian's WSGI interface flushes existing synchronous `StreamingHttpResponse` Datastar SSE generators immediately with zero view/ORM changes.
2. **Option B (Python `async def` SSE generators under ASGI)**: Keep `granian --interface asgi` and evaluate converting only the Python generator functions passed to `StreamingHttpResponse` from `def` to `async def` (using `subscribe_pg_events_async` while emitting the exact same Datastar `text/event-stream` wire frames), auditing any in-generator Django ORM reads for `SynchronousOnlyOperation` safety.

## Tags
demo_ui, metacognition, work_organisation, benchmarking, deploy, datastar, sse, granian, asgi, wsgi, architecture, discussion

## Status
pending
