"""
Helpers for Work Organisation trial doctests.

Produces metacognition-quality reports: full execution traces with verbatim
AI cluster syntheses, real timing data, captured screenshots, and model identification.
"""

import os
import time
import datetime
import logging
import textwrap
from contextlib import contextmanager
from pathlib import Path
from PIL import Image

logger = logging.getLogger(__name__)

TRIALS_DIR = Path(__file__).resolve().parent
SCREENSHOTS_DIR = TRIALS_DIR / "screenshots"
DOCS_IMAGES_DIR = TRIALS_DIR.parent.parent / "documentation" / "source" / "_images" / "work_organisation"

SCREENSHOTS_DIR.mkdir(parents=True, exist_ok=True)
DOCS_IMAGES_DIR.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Timing
# ---------------------------------------------------------------------------

@contextmanager
def timed_step(label: str, steps_recorded: list):
    """Context manager that records wall-clock duration for a trial step.

    Usage::

        with timed_step("Idea Entry", steps_recorded) as step:
            ...do work...
            step["description"] = "What happened"
            step["image"] = img_path
    """
    entry = {
        "step": label,
        "description": "",
        "image": "",
        "researcher_thinking": "",
        "action_taken": "",
        "ai_synthesis": "",
        "researcher_evaluation": "",
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
    Takes a stabilized full-page screenshot (1280x800) and saves to both trials and docs directories.
    Ensures network idle, web fonts ready, and wait_selector presence to avoid asset-loading flicker.
    """
    filename = f"{name}.png"
    trial_path = SCREENSHOTS_DIR / filename
    docs_path = DOCS_IMAGES_DIR / filename

    try:
        page.wait_for_load_state("domcontentloaded", timeout=2000)
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


def create_animated_gif(image_paths: list, output_name: str, duration_ms: int = 1400) -> str:
    """Stitches a list of PNG screenshot paths into an animated GIF."""
    if not image_paths:
        return ""
    frames = [Image.open(p) for p in image_paths if os.path.exists(p)]
    if not frames:
        return ""

    filename = f"{output_name}.gif"
    trial_path = SCREENSHOTS_DIR / filename
    docs_path = DOCS_IMAGES_DIR / filename

    frames[0].save(
        str(trial_path),
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    frames[0].save(
        str(docs_path),
        save_all=True,
        append_images=frames[1:],
        duration=duration_ms,
        loop=0,
        optimize=True,
    )
    return str(docs_path)


# ---------------------------------------------------------------------------
# RST Report generation
# ---------------------------------------------------------------------------

def _rst_heading(text: str, char: str) -> str:
    """Helper to produce an RST section heading."""
    return f"{text}\n{char * len(text)}\n\n"


def wrap_prose_block(text: str, width: int = 90) -> str:
    """Wraps long prose paragraphs at `width` columns while preserving code blocks and lists."""
    if not text:
        return ""
    wrapped_lines = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            wrapped_lines.append("")
            continue
        if raw_line.count("|") >= 2:
            wrapped_lines.append(raw_line)
            continue
        stripped = raw_line.strip()
        if (
            raw_line.startswith("    ")
            or raw_line.startswith("\t")
            or stripped.startswith(("def ", "class ", "import ", "from ", ">>>", "...", "return "))
            or set(stripped).issubset({"-", "=", "_", "*", "#"})
        ):
            wrapped_lines.append(raw_line)
            continue

        if len(raw_line) > width:
            subsequent_indent = ""
            if stripped.startswith(("- ", "* ", "+ ")):
                prefix_len = len(raw_line) - len(stripped) + 2
                subsequent_indent = " " * prefix_len
            filled = textwrap.fill(
                raw_line,
                width=width,
                subsequent_indent=subsequent_indent,
                break_long_words=False,
                break_on_hyphens=False,
            )
            wrapped_lines.append(filled)
        else:
            wrapped_lines.append(raw_line)
    return "\n".join(wrapped_lines)


def record_ui_doctest_run(
    trial_name: str,
    title: str,
    steps_data: list,
    *,
    status: str = "passed",
    model_name: str = "",
    total_duration_s: float | None = None,
    scenario_description: str = "",
    checks: list | None = None,
) -> str:
    """
    Generates a structured Sphinx .rst report for the Work Organisation UI doctest run.
    """
    report_path = TRIALS_DIR / f"{trial_name}_report.rst"
    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if total_duration_s is None:
        total_duration_s = sum(s.get("duration_s", 0) for s in steps_data)

    scenario_text = scenario_description or (
        "An autonomous robotics research team is formulating sensor configurations and spatial "
        "perception strategies for a search-and-rescue rover operating in collapsed structures. "
        "The researcher uses the Work Organisation whiteboard to brainstorm ideas one by one, "
        "applies AI clustering to identify redundancies and synthesize thematic categories, "
        "and exercises the canvas affordances to re-organize and dissolve clusters."
    )

    with open(report_path, "w", encoding="utf-8") as f:
        # Title
        border = "=" * (len(title) + 18)
        f.write(f"{border}\n")
        f.write(f"Doctest Report: {title}\n")
        f.write(f"{border}\n\n")

        f.write(f"**Date**: {now_str}\n\n")
        if model_name:
            f.write(f"**Local Model**: {model_name}\n\n")
        f.write(f"**Outcome**: {status.upper()}\n\n")
        f.write(f"**Total Execution Time**: {total_duration_s:.1f}s\n\n")

        # Scenario
        f.write(_rst_heading("Scenario & Motivating Task", "~"))
        f.write(f"{wrap_prose_block(scenario_text)}\n\n")

        # Animated GIF
        image_paths = [s["image"] for s in steps_data if s.get("image")]
        if len(image_paths) > 1:
            gif_path = create_animated_gif(image_paths, f"{trial_name}_animated")
            if gif_path:
                rel_gif = f"../_images/work_organisation/{Path(gif_path).name}"
                f.write(_rst_heading("Animated Walkthrough", "~"))
                f.write(f".. image:: {rel_gif}\n")
                f.write(f"   :width: 780\n")
                f.write(f"   :alt: Animated Walkthrough: {title}\n\n")

        # Execution Steps
        f.write(_rst_heading("Interaction Trace: Researcher & Whiteboard Canvas", "-"))

        for i, s in enumerate(steps_data, 1):
            f.write(_rst_heading(f"Step {i}: {s['step']} ({s['duration_s']:.1f}s)", "^"))

            if s.get("researcher_thinking"):
                f.write("**Researcher Intent**::\n\n")
                for line in wrap_prose_block(s["researcher_thinking"]).splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")

            if s.get("action_taken"):
                f.write(f"**Affordance & Action**: {s['action_taken']}\n\n")

            if s.get("description"):
                f.write(f"{wrap_prose_block(s['description'])}\n\n")

            if s.get("ai_synthesis"):
                f.write("**AI Model Synthesis & Deduplication**::\n\n")
                for line in wrap_prose_block(s["ai_synthesis"]).splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")

            if s.get("researcher_evaluation"):
                f.write("**Evaluation & State Verification**::\n\n")
                for line in wrap_prose_block(s["researcher_evaluation"]).splitlines():
                    f.write(f"    {line}\n")
                f.write("\n")

            if s.get("image"):
                rel_img = f"../_images/work_organisation/{Path(s['image']).name}"
                f.write(f".. image:: {rel_img}\n")
                f.write(f"   :width: 720\n")
                f.write(f"   :alt: Step {i} Screenshot: {s['step']}\n\n")

        # Invariants & Checks
        if checks:
            f.write(_rst_heading("Verification & Invariants Check", "~"))
            f.write(".. list-table:: Invariant Verification Matrix\n")
            f.write("   :header-rows: 1\n")
            f.write("   :widths: 40 15 45\n\n")
            f.write("   * - Invariant\n")
            f.write("     - Status\n")
            f.write("     - Details\n")
            for c in checks:
                name = c.get("name", "Check")
                c_status = "PASS" if c.get("passed", False) else "FAIL"
                detail = c.get("detail", "")
                f.write(f"   * - {name}\n")
                f.write(f"     - {c_status}\n")
                f.write(f"     - {detail}\n")
            f.write("\n")

    return str(report_path)
