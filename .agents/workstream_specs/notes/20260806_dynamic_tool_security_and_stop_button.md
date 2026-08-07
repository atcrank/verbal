# Note

## Timestamp
2026-08-06T12:22:05+10:00

## User says
Lets /take_a_note of that.  This task should perhaps consider adding a "stop" button in case the user is not happy with how long the blueprint is taking.

## Current context
We just audited the system and realized that human supervision for `dynamic_tools` is not effectively implemented. `manage_dynamic_tools` explicitly bypasses the `requires_approval` flag, and `compiler.py` completely ignores the `requires_approval` flag during execution. The user agreed with the philosophy of supervising dynamic tools and asked to note the issue along with a suggestion to add a "stop" button to halt long-running blueprints.

## What needs to be done
We need to implement human-in-the-loop authorization for LangGraph tool execution. This involves:
1. Updating `manage_dynamic_tools` to set `requires_approval = True`.
2. Updating `compiler.py` `_action_node` to halt graph execution (e.g., `route_to="USER_INPUT_REQUIRED"`) if a tool requires approval, waiting for user authorization.
3. Adding a general "stop" button in the UI/execution flow to allow a user to manually interrupt a long-running blueprint.

## Tags
metacognition, compiler, meta_tools, security, bug, feature, human-in-the-loop

## Status
pending
