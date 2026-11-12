"""Shared runtime fact collection and independent post-trial review helpers.

Evaluates actual sandbox outputs, workspace artifacts, and explicit checks across
both metacognition doctests and Demo UI doctests, and optionally runs a separate
isolated Conversation to compare expected vs. observed model behavior.
"""

import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _scan_monologue(entries: List[dict], summary: Dict[str, Any]) -> None:
    """Recursively extracts execution and evaluation signals from monologue entries."""
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        step_name = str(entry.get("step_name", ""))
        output = str(entry.get("output", ""))
        tool_res = str(entry.get("tool_result", ""))
        combined = f"{output}\n{tool_res}"

        sb_succ = combined.count("Sandbox Execution Succeeded")
        sb_fail = (
            combined.count("Sandbox Execution Failed")
            + combined.count("Refusing to execute empty Python script")
        )
        if sb_fail == 0 and "SyntaxError" in tool_res:
            sb_fail = 1
        if sb_succ or sb_fail:
            summary["sandbox_runs"] += sb_succ + sb_fail
            summary["sandbox_successes"] += sb_succ
            summary["sandbox_failures"] += sb_fail
            summary["sandbox_outputs"].append(tool_res or output)

        if step_name.endswith("(Evaluation)") or "Evaluation Result:" in output:
            summary["eval_verdicts"].append(output.strip())
            if entry.get("failed") or "Evaluation Result: FAILURE" in output:
                summary["eval_failures"] += 1

        if entry.get("failed"):
            summary["failed_steps"].append(step_name or "Unknown")

        if "ToolCallMissing" in combined or "ABORTED:" in output:
            summary["harness_anomalies"].append(f"{step_name}: {output[:240]}")

        sub_mono = entry.get("sub_monologue")
        if isinstance(sub_mono, list):
            _scan_monologue(sub_mono, summary)


def _scan_workspace(workspace_dir: Optional[str]) -> Dict[str, Any]:
    """Inspects generated files on disk without crossing .git metadata."""
    info: Dict[str, Any] = {"files": [], "empty_files": [], "csv_ellipsis": []}
    if not workspace_dir or not os.path.exists(workspace_dir):
        return info

    for root, _, files in os.walk(workspace_dir):
        if ".git" in root.split(os.sep):
            continue
        for fname in sorted(files):
            fpath = os.path.join(root, fname)
            rel = os.path.relpath(fpath, workspace_dir)
            try:
                with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
                info["files"].append(rel)
                if not content.strip():
                    info["empty_files"].append(rel)
                if fname.endswith(".csv") and "Ellipsis" in content:
                    info["csv_ellipsis"].append(rel)
            except Exception as exc:
                info["files"].append(f"{rel} (unreadable: {exc})")
    return info


def collect_runtime_facts(
    result: Optional[dict] = None,
    workspace_dir: Optional[str] = None,
    checks: Optional[List[Dict[str, Any]]] = None,
    steps_data: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Aggregates programmatic execution evidence and computes honest model outcome."""
    res = result or {}
    summary: Dict[str, Any] = {
        "has_error": "error" in res,
        "error": res.get("error", ""),
        "route_to": res.get("route_to", ""),
        "sandbox_runs": 0,
        "sandbox_successes": 0,
        "sandbox_failures": 0,
        "sandbox_outputs": [],
        "eval_verdicts": [],
        "eval_failures": 0,
        "failed_steps": [],
        "harness_anomalies": [],
    }

    monologue = res.get("internal_monologue") or []
    _scan_monologue(monologue, summary)

    # Also inspect Demo UI recorded steps if provided (e.g., direct sandbox/DOM captures)
    for st in steps_data or []:
        sb = str(st.get("sandbox_output", ""))
        audit = str(st.get("adversarial_audit", ""))
        if sb and ("RC=1" in sb or "SyntaxError" in sb or "Execution Failed" in sb):
            if summary["sandbox_failures"] == 0:
                summary["sandbox_runs"] += 1
                summary["sandbox_failures"] += 1
                summary["sandbox_outputs"].append(sb)
        elif sb and "Sandbox Execution Succeeded" in sb and summary["sandbox_runs"] == 0:
            summary["sandbox_runs"] += 1
            summary["sandbox_successes"] += 1
            summary["sandbox_outputs"].append(sb)
        if "LOGIC AUDIT FAILED" in audit:
            summary["eval_failures"] += 1

    ws_info = _scan_workspace(workspace_dir)
    summary["workspace"] = ws_info
    check_list = list(checks or [])
    summary["checks"] = check_list

    failed_checks = [c for c in check_list if not c.get("passed")]
    passed_checks = [c for c in check_list if c.get("passed")]
    last_eval_failed = bool(monologue and monologue[-1].get("failed"))
    final_resp = str(res.get("final_response", ""))

    if summary["has_error"]:
        outcome = "ERROR"
    elif (
        failed_checks
        or summary["route_to"] == "FAILURE"
        or last_eval_failed
        or (summary["sandbox_runs"] > 0 and summary["sandbox_successes"] == 0)
        or bool(ws_info["csv_ellipsis"])
        or ("Evaluation Notice" in final_resp and "did not satisfy" in final_resp)
    ):
        outcome = "FAILED"
    elif summary["sandbox_failures"] > 0 or summary["eval_failures"] > 0 or bool(ws_info["empty_files"]):
        outcome = "PARTIAL"
    elif check_list and len(passed_checks) == len(check_list):
        outcome = "PASSED"
    elif check_list:
        outcome = "PARTIAL"
    else:
        outcome = "PASSED"

    summary["model_outcome"] = outcome
    return summary


def format_facts_summary(facts: Dict[str, Any], expected_outcome: str = "") -> str:
    """Formats deterministic runtime facts cleanly for reviewer prompts and RST reports."""
    ws = facts.get("workspace", {})
    exp_lower = (expected_outcome or "").lower()
    sandbox_relevant = facts.get("sandbox_runs", 0) > 0 or "sandbox" in exp_lower or "python" in exp_lower
    lines = [
        f"- Model Task Outcome: {facts.get('model_outcome', 'UNKNOWN')}",
        f"- Route / Status: {facts.get('route_to') or 'n/a'} (error={facts.get('has_error', False)})",
    ]
    if sandbox_relevant:
        lines.append(
            f"- Sandbox Executions: {facts.get('sandbox_runs', 0)} total "
            f"({facts.get('sandbox_successes', 0)} succeeded, {facts.get('sandbox_failures', 0)} failed)"
        )
    lines.append(f"- Workspace Files: {', '.join(ws.get('files', [])) or 'None'}")
    if ws.get("empty_files"):
        lines.append(f"- Empty Workspace Files: {', '.join(ws['empty_files'])}")
    if ws.get("csv_ellipsis"):
        lines.append(f"- Corrupted CSV Ellipsis Artifacts: {', '.join(ws['csv_ellipsis'])}")
    if facts.get("eval_failures"):
        lines.append(f"- Evaluator Rejections Recorded: {facts['eval_failures']}")
    if facts.get("harness_anomalies"):
        lines.append(f"- Harness Anomalies: {'; '.join(facts['harness_anomalies'])}")

    for chk in facts.get("checks", []):
        tag = "PASS" if chk.get("passed") else "FAIL"
        detail = f" ({chk['detail']})" if chk.get("detail") else ""
        lines.append(f"- Check [{tag}] {chk.get('label', 'check')}{detail}")

    return "\n".join(lines)


def _is_mocked_ai_service(ai_service) -> bool:
    """Detects whether AIService.generate_response2 is patched with a mock in tests."""
    from unittest.mock import NonCallableMock
    gen = getattr(ai_service, "generate_response2", None)
    cls_gen = getattr(type(ai_service), "generate_response2", None) if ai_service else None
    return isinstance(ai_service, NonCallableMock) or isinstance(gen, NonCallableMock) or isinstance(cls_gen, NonCallableMock)


def run_post_trial_comparison(
    trial_name: str,
    prompts: List[str],
    expected_outcome: str,
    observed_response: str,
    facts: Dict[str, Any],
    is_mocked: bool = False,
) -> str:
    """Runs an independent Conversation comparing expected results to observed runtime output.

    Uses a dedicated `doctest_reviewer` user so audit logs do not pollute user RAG history
    or Demo UI conversation sidebars.
    """
    facts_block = format_facts_summary(facts, expected_outcome=expected_outcome)
    if not expected_outcome and not facts.get("checks"):
        return f"Deterministic Verification Summary:\n{facts_block}"

    from llm_api.apps import service_registry
    ai_service = getattr(service_registry, "ai_service", None)

    if is_mocked or not ai_service or _is_mocked_ai_service(ai_service):
        return (
            f"Deterministic Verification Summary:\n{facts_block}\n\n"
            f"Expected Outcome Specification:\n{expected_outcome or '(not specified)'}\n\n"
            "*(Independent LLM review skipped because trial ran with mocked inference.)*"
        )

    sandbox_tail = "\n---\n".join(facts.get("sandbox_outputs", [])[-2:])[:1500]
    prompts_txt = "\n".join(f"Turn {i+1}: {p}" for i, p in enumerate(prompts))[:1200]
    observed_txt = (observed_response or "(empty response)")[:2200]

    reviewer_sys = (
        "You are an impartial scientific benchmark reviewer writing a concise closing teaching note "
        "comparing the Expected Results against what the tested model actually produced.\n"
        "Rules:\n"
        "1. Trust the Deterministic Verification Summary and Sandbox Outputs over the model's prose claims.\n"
        "2. State clearly what the expected result was, what the model actually achieved, and any specific "
        "calculation error, syntax failure, skipped execution, or hallucination debunked by the facts.\n"
        "3. Keep your commentary to 1-2 tight, factual paragraphs useful to a developer evaluating small vs larger models."
    )
    reviewer_user = (
        f"Trial: {trial_name}\n\n"
        f"Original Prompt(s):\n{prompts_txt}\n\n"
        f"Expected Result:\n{expected_outcome or 'See deterministic checks below.'}\n\n"
        f"Deterministic Verification Summary:\n{facts_block}\n\n"
        f"Actual Sandbox Outputs:\n{sandbox_tail or '(none recorded)'}\n\n"
        f"Model Observed Final Response:\n{observed_txt}"
    )

    try:
        from django.contrib.auth import get_user_model
        from llm_api.models import Conversation, PromptResponseLog

        User = get_user_model()
        reviewer, _ = User.objects.get_or_create(
            username="doctest_reviewer",
            defaults={"is_staff": False, "is_superuser": False},
        )
        review_conv = Conversation.objects.create(
            user=reviewer,
            title=f"Post-Trial Review: {trial_name[:60]}",
        )
        messages = [
            {"role": "system", "content": reviewer_sys},
            {"role": "user", "content": reviewer_user},
        ]
        [raw] = ai_service.generate_response2(
            messages,
            max_new_tokens=450,
            log_kwargs={
                "conversation_id": str(review_conv.id),
                "user_id": reviewer.id,
                "user_prompt": reviewer_user,
                "log_kind": PromptResponseLog.LogKind.TURN,
            },
        )
        review_text = ai_service.clean_response(raw).strip()
        if not review_text:
            review_text = "(Reviewer model returned an empty critique.)"
    except Exception as exc:
        logger.warning("Post-trial review Conversation failed for %s: %s", trial_name, exc)
        review_text = f"*(Independent reviewer Conversation unavailable: {exc})*"

    return (
        f"Expected Outcome Specification:\n{expected_outcome or '(none specified)'}\n\n"
        f"Deterministic Verification Summary:\n{facts_block}\n\n"
        f"Independent Review (Separate Conversation):\n{review_text}"
    )

