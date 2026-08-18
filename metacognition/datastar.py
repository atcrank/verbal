"""
Datastar Server-Sent Events (SSE) Helper Module.
Implements the SSE framing protocol specified by Datastar (https://data-star.dev).
"""
import json
from typing import Dict, Any, Optional, Union, List


class DatastarSSE:
    """
    Utility class for creating standard Datastar SSE frames for streaming responses.
    """

    @staticmethod
    def merge_fragments(
        html: str,
        selector: Optional[str] = None,
        merge_mode: str = "morph",
        settle_duration: Optional[int] = None,
        use_view_transition: bool = False
    ) -> str:
        """
        Creates an SSE frame to merge or patch HTML fragments into the DOM.
        
        merge_mode options:
          - morph: (default) morph existing element with new fragment
          - inner: replace innerHTML of target
          - outer: replace outerHTML of target
          - prepend: insert before first child
          - append: insert after last child
          - before: insert before target element
          - after: insert after target element
          - delete: remove target element
          - upsertAttributes: update attributes without replacing body
        """
        lines = ["event: datastar-merge-fragments"]
        
        if selector:
            lines.append(f"data: selector {selector}")
        if merge_mode != "morph":
            lines.append(f"data: mergeMode {merge_mode}")
        if settle_duration is not None:
            lines.append(f"data: settleDuration {settle_duration}")
        if use_view_transition:
            lines.append("data: useViewTransition true")
            
        # Fragments can be multi-line
        for line in html.strip().splitlines():
            lines.append(f"data: fragments {line}")
            
        lines.append("\n") # Blank line indicates end of SSE event
        return "\n".join(lines)

    @staticmethod
    def merge_signals(
        signals: Dict[str, Any],
        only_if_missing: bool = False
    ) -> str:
        """
        Creates an SSE frame to update reactive client-side signals in the Datastar store.
        """
        lines = ["event: datastar-merge-signals"]
        if only_if_missing:
            lines.append("data: onlyIfMissing true")
            
        signals_json = json.dumps(signals)
        lines.append(f"data: signals {signals_json}")
        lines.append("\n")
        return "\n".join(lines)

    @staticmethod
    def execute_script(
        script: str,
        auto_remove: bool = True
    ) -> str:
        """
        Creates an SSE frame to execute JavaScript in the browser.
        """
        lines = ["event: datastar-execute-script"]
        if not auto_remove:
            lines.append("data: autoRemove false")
            
        for line in script.strip().splitlines():
            lines.append(f"data: script {line}")
            
        lines.append("\n")
        return "\n".join(lines)
