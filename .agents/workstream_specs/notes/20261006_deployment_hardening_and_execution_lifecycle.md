# Deployment Hardening & Cognitive Execution Lifecycle Notes

## Timestamp
2026-10-06 10:05:00 AEST

## Context
During live multi-instance deployment testing (Web, Worker, Inference, Sandbox, and PostgreSQL), 11 operational and user experience items were identified across data lineage, sandbox failure modes, graph termination semantics, SSE stream reliability, and Demo UI feedback.

See the full workstream specification at [ws20_deployment_hardening_and_execution_lifecycle.md](file:///home/crank/coding/antigrav/verbal/.agents/workstream_specs/ws20_deployment_hardening_and_execution_lifecycle.md).

## Key Principles & Invariants for WS20
1. **Zero Silent Looping on Offline Infrastructure**: When an external service (such as the Docker code sandbox) is offline or unreachable, tools must fail fast and route to `FAILURE` with a descriptive message rather than looping on `"route_to": "SELF"`.
2. **Deterministic Data Lineage**: Every turn and step executed by a blueprint must record its `blueprint` FK, `reasoning_step` FK, and chain `parent_log` to form an authentic DAG in `PromptResponseLog`.
3. **Clean Persistent State**: Initial placeholder records must be overwritten with clean response text upon async task completion; raw streaming HTML tags must not remain as the final stored response.
4. **Natural Graph Termination**: Blueprint execution should terminate naturally through LangGraph terminal nodes and `END` transitions rather than requiring dummy `TASK_COMPLETE` tool invocations.
5. **Synchronous SSE Consumers**: Under WSGI Django `runserver`, SSE endpoints must use synchronous generators (`subscribe_pg_events_sync`) rather than async generators to prevent stalled or dropped PostgreSQL notifications.
