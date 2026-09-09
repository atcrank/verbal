"""
Helpers for Demo UI trial doctests.

Produces metacognition-quality reports: full execution traces with verbatim
AI responses, captured sandbox output, real timing data, and model identification.
"""

import os
import time
import datetime
import logging
from contextlib import contextmanager
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)

TRIALS_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = TRIALS_DIR / "screenshots"
DOCS_IMAGES_DIR = TRIALS_DIR.parent.parent / "documentation" / "source" / "_images" / "demo_ui"

SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
DOCS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

@contextmanager
def timed_step(label: str, steps_recorded: list):
    """Context manager that records wall-clock duration for a trial step.

    Usage::

        with timed_step("Ingestion", steps_recorded) as step:
            ...do work...
            step["description"] = "What happened"
            step["image"] = img_path
    """
    entry = {
        "step": label,
        "description": "",
        "image": "",
        "response_text": "",
        "code_text": "",
        "sandbox_output": "",
        "rag_chunks": [],
        "model_name": "",
        "adversarial_audit": "",
        "corrective_inquiry": "",
        "protocol_synthesis": "",
        "duration_s": 0.0,
    }
    t0 = time.perf_counter()
    try:
        yield entry
    finally:
        entry["duration_s"] = round(time.perf_counter() - t0, 2)
        steps_recorded.append(entry)


# ---------------------------------------------------------------------------
# Screenshot capture
# ---------------------------------------------------------------------------

def take_ui_screenshot(page, name: str, wait_selector: str = None) -> str:
    """
    Takes a stabilized full page screenshot (1280x800) and saves to both trials and docs directories.
    Ensures network idle, web fonts ready, and wait_selector presence to avoid asset-loading flicker.
    """
    filename = f"{name}.png"
    trial_path = SCREENSHOTS_DIR / filename
    docs_path = DOCS_IMAGES_DIR / filename

    # Wait for network idle and web fonts to prevent unstyled layout shifts
    try:
        page.wait_for_load_state("networkidle", timeout=4000)
    except Exception:
        pass

    try:
        page.evaluate("document.fonts ? document.fonts.ready : Promise.resolve()")
    except Exception:
        pass

    if wait_selector:
        try:
            page.wait_for_selector(wait_selector, timeout=4000)
        except Exception:
            pass

    page.screenshot(path=str(trial_path), full_page=True)
    page.screenshot(path=str(docs_path), full_page=True)
    return str(docs_path)


# ---------------------------------------------------------------------------
# AI response capture
# ---------------------------------------------------------------------------

def capture_ai_response(page, selector: str = ".chat-message.ai .bubble") -> str:
    """Extracts the substantive text content of the most recent AI response bubble,
    cleanly stripping out UI action buttons and token stats."""
    try:
        clean_text = page.evaluate(f"""() => {{
            const bubbles = document.querySelectorAll('{selector}');
            if (!bubbles || bubbles.length === 0) return '';
            const bubble = bubbles[bubbles.length - 1];
            const clone = bubble.cloneNode(true);
            const actions = clone.querySelector('.message-actions-bar');
            if (actions) actions.remove();
            return clone.innerText.trim();
        }}""")
        if clean_text:
            return clean_text.strip()
    except Exception as exc:
        logger.warning("Could not capture AI response via evaluate: %s", exc)

    try:
        loc = page.locator(selector)
        count = loc.count()
        if count == 0:
            return ""
        raw = (loc.nth(count - 1).text_content() or "").strip()
        lines = [
            line for line in raw.splitlines()
            if not line.strip().startswith("Branch from here")
            and not line.strip().startswith("Tokens:")
        ]
        return "\n".join(lines).strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Animated GIF generation
# ---------------------------------------------------------------------------

# 3 seconds per frame – fast enough to scan, slow enough to read headlines
DEFAULT_GIF_FRAME_MS = 3000

def create_animated_gif(frame_paths: list, name: str, duration_ms: int = DEFAULT_GIF_FRAME_MS) -> str:
    """
    Combines screenshot frames into an animated GIF.
    Defaults to 3000ms (3 seconds) per frame.
    """
    if not frame_paths:
        return ""

    filename = f"{name}.gif"
    trial_path = SCREENSHOTS_DIR / filename
    docs_path = DOCS_IMAGES_DIR / filename

    frames = [Image.open(p) for p in frame_paths if os.path.exists(p)]
    if not frames:
        return ""

    frames[0].save(
        str(trial_path),
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True
    )
    frames[0].save(
        str(docs_path),
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True
    )
    return str(docs_path)


# ---------------------------------------------------------------------------
# RST Report generation  (metacognition-quality, Role 3 & 4 focused)
# ---------------------------------------------------------------------------

def _rst_heading(text: str, char: str) -> str:
    """Helper to produce an RST section heading."""
    return f"{text}\n{char * len(text)}\n\n"


def _rst_code_block(text: str, indent: str = "    ") -> str:
    """Wraps text in an RST literal block (``::`` directive)."""
    lines = text.splitlines() if text else ["(empty)"]
    body = "\n".join(f"{indent}{line}" for line in lines)
    return f"::\n\n{body}\n\n"


def record_ui_doctest_run(
    trial_name: str,
    title: str,
    steps_data: list,
    *,
    status: str = "PASSED",
    model_name: str = "",
    total_duration_s: float | None = None,
    blueprint_result: dict | None = None,
    scenario_description: str = "",
):
    """
    Generates a structured Sphinx .rst report for the UI doctest run.

    Centers on Roles 3 & 4:
    - The Researcher: Formulating causal hypotheses, experimental design, and prompts.
    - Verbal / Reason: Local Gemma4 model reasoning, composing Python in sandbox,
      and yielding tabulations and recommendations.
    """
    report_path = TRIALS_DIR / f"{trial_name}_report.rst"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if total_duration_s is None:
        total_duration_s = sum(s.get("duration_s", 0) for s in steps_data)

    default_scenario = (
        "We imagine a researcher who has the following task and resources: An autonomous robotics research "
        "team is designing an empirical intervention trial for a tracked search-and-rescue robot operating inside "
        "a multi-story collapsed structure under heavy smoke and rubble. The researcher uses the Verbal / Reason "
        "interface to formulate hypotheses, evaluate causal and statistical trade-offs, compute factorial parameters "
        "in the sandbox, and structure the domain knowledge graph."
    )
    scenario_text = scenario_description or default_scenario

    with open(report_path, "w", encoding="utf-8") as f:
        # Header
        border = "=" * (len(title) + 18)
        f.write(f"{border}\n")
        f.write(f"Doctest Report: {title}\n")
        f.write(f"{border}\n\n")

        f.write(f"**Date**: {now_str}\n\n")
        if model_name:
            f.write(f"**Local Model**: {model_name}\n\n")
        f.write(f"**Status**: {status}\n\n")
        f.write(f"**Total Execution Time**: {total_duration_s:.1f}s\n\n")

        # Scene setting
        f.write(_rst_heading("Scenario & Research Task", "~"))
        f.write(f"{scenario_text}\n\n")

        # Animated GIF
        image_paths = [s["image"] for s in steps_data if s.get("image")]
        if len(image_paths) > 1:
            gif_path = create_animated_gif(image_paths, f"{trial_name}_animated")
            if gif_path:
                rel_gif = f"../_images/demo_ui/{Path(gif_path).name}"
                f.write(_rst_heading("Animated Walkthrough", "~"))
                f.write(f".. image:: {rel_gif}\n")
                f.write(f"   :width: 780\n")
                f.write(f"   :alt: Animated Walkthrough: {title}\n\n")

        # Execution steps (Dialogue between Researcher and Verbal / Reason)
        f.write(_rst_heading("Interaction Trace: Researcher & Verbal / Reason", "-"))

        for i, step in enumerate(steps_data, 1):
            step_label = step.get("step", f"Step {i}")
            duration = step.get("duration_s", 0)
            f.write(_rst_heading(f"Step {i}: {step_label} ({duration:.1f}s)", "^"))

            # 1. The Researcher: Thinking & Inquiry
            r_thinking = step.get("researcher_thinking", "")
            if r_thinking:
                f.write(f"**The Researcher (Thinking & Formulation)**:\n\n{r_thinking}\n\n")
            elif step.get("description"):
                f.write(f"{step['description']}\n\n")

            r_inquiry = step.get("researcher_inquiry", "")
            if r_inquiry:
                f.write("**Researcher Inquiry (Entered in Demo UI)**:\n\n")
                f.write(_rst_code_block(r_inquiry))

            # RAG chunks retrieved
            chunks = step.get("rag_chunks", [])
            if chunks:
                f.write("**Retrieved Literature Context (RAG Recall)**:\n\n")
                for chunk in chunks:
                    preview = chunk.get("preview", chunk.get("text", ""))[:200]
                    score = chunk.get("score", "")
                    score_str = f" (score: {score:.2f})" if isinstance(score, float) else ""
                    citation = chunk.get("citation", "")
                    cite_str = f"**{citation}** " if citation else ""
                    f.write(f"- {cite_str}[Chunk `{chunk.get('id', 'unknown')}`{score_str}]: \"{preview}\"\n")
                f.write("\n")

            # 2. Verbal / Reason: Local Model Execution
            step_model = step.get("model_name") or model_name or "Gemma4"
            bp_name = step.get("blueprint_name", "")
            if bp_name:
                f.write(f"**Verbal / Reason (Cognitive Blueprint: '{bp_name}')**\n\n")

            # Generated code (composed by local model)
            code = step.get("code_text", "")
            if code:
                f.write(f"**Python Simulation Code (Composed by: {step_model})**:\n\n")
                f.write(f".. code-block:: python\n\n")
                for line in code.splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")

            # Sandbox output
            sandbox = step.get("sandbox_output", "")
            if sandbox:
                f.write("**Docker Sandbox Execution Output (verbal_sandbox)**:\n\n")
                f.write(_rst_code_block(sandbox))

            # AI response (verbatim synthesis)
            response = step.get("response_text", "")
            if response:
                f.write(f"**Verbal / Reason Response (Synthesized by: {step_model})**:\n\n")
                f.write(_rst_code_block(response))

            # Active Adversarial Verification / Logic Audit
            audit = step.get("adversarial_audit", "")
            if audit:
                f.write("**Active Adversarial Verification & Logic Audit**:\n\n")
                f.write(".. warning::\n\n")
                for line in audit.splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")

            # Corrective Follow-Up Inquiry
            corr_inq = step.get("corrective_inquiry", "")
            if corr_inq:
                f.write("**Researcher Corrective Follow-Up Inquiry (Submitted in Demo UI)**:\n\n")
                f.write(_rst_code_block(corr_inq))

            # 3. Researcher Evaluation
            r_eval = step.get("researcher_evaluation", "")
            if r_eval:
                f.write(f"**The Researcher (Evaluation & Decision)**:\n\n{r_eval}\n\n")

            # Terminal Empirical Intervention Protocol Synthesis
            protocol = step.get("protocol_synthesis", "")
            if protocol:
                f.write("**Empirical Intervention Protocol Synthesis**:\n\n")
                f.write(".. note::\n\n")
                for line in protocol.splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")

            # Screenshot
            img = step.get("image", "")
            if img:
                rel_img = f"../_images/demo_ui/{Path(img).name}"
                f.write(f".. image:: {rel_img}\n")
                f.write(f"   :width: 750\n")
                f.write(f"   :alt: {step_label}\n\n")

        # Blueprint execution trace (LangGraph granular steps)
        if blueprint_result:
            f.write(_rst_heading("Blueprint Granular Execution Trace", "-"))

            monologue = blueprint_result.get("internal_monologue", [])
            if monologue:
                for j, entry in enumerate(monologue, 1):
                    step_name = entry.get("step_name", "Unknown")
                    entry_model = entry.get("model_name", "") or model_name
                    f.write(_rst_heading(f"Blueprint Step {j}: {step_name}", "^"))

                    if entry_model:
                        f.write(f"*(Generated by: {entry_model})*\n\n")

                    if entry.get("system_prompt"):
                        f.write("**System Instructions**:\n\n")
                        f.write(_rst_code_block(str(entry["system_prompt"])))

                    if entry.get("user_prompt"):
                        f.write("**User Prompt & Working State**:\n\n")
                        f.write(_rst_code_block(str(entry["user_prompt"])))

                    if entry.get("output"):
                        f.write("**Model Output**:\n\n")
                        f.write(_rst_code_block(str(entry["output"])))

                    if entry.get("tool_result"):
                        f.write("**Tool Execution Result**:\n\n")
                        f.write(_rst_code_block(str(entry["tool_result"])))
            else:
                f.write("No granular steps recorded in blueprint result.\n\n")

            # Final response
            final = blueprint_result.get("final_response", "")
            if final:
                f.write(_rst_heading("Final Blueprint Response", "^"))
                f.write(_rst_code_block(str(final)))

            # Workspace files
            cid = blueprint_result.get("conversation_id")
            workspace_dir = os.path.join("workspaces", str(cid)) if cid else None
            if workspace_dir and os.path.exists(workspace_dir):
                f.write(_rst_heading("Generated Workspace Files", "^"))
                for root, dirs, files in os.walk(workspace_dir):
                    if ".git" in root:
                        continue
                    for file_name in files:
                        file_path = os.path.join(root, file_name)
                        rel_path = os.path.relpath(file_path, workspace_dir)
                        f.write(f"File: {rel_path}\n")
                        f.write(f"{'~' * (len(rel_path) + 6)}\n\n")
                        try:
                            with open(file_path, "r", encoding="utf-8") as code_file:
                                f.write(_rst_code_block(code_file.read()))
                        except Exception as exc:
                            f.write(f"*[Error reading file: {exc}]*\n\n")

        f.write("\n")

    return str(report_path)
