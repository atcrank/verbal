"""
Datastar Server-Sent Events (SSE) Helper Module.
Implements the SSE framing protocol specified by Datastar (https://data-star.dev).
"""
import json
from typing import Dict, Any, Optional, Union, List


class DatastarSSE:
    """
    Utility class for creating standard Datastar SSE frames for streaming responses.
    Follows Datastar hypermedia specifications (https://data-star.dev/reference/sse_events).
    """

    @classmethod
    def patch_elements(
        cls,
        elements: str,
        selector: Optional[str] = None,
        mode: str = "morph",
        settle_duration: Optional[int] = None,
        use_view_transition: bool = False,
    ) -> str:
        """
        Creates an SSE frame to patch/merge HTML elements into the DOM.
        (https://data-star.dev/reference/sse_events#datastar-patch-elements)
        
        mode options (default is 'morph'):
          - morph: (default) morph existing element with new element in place
          - inner: replace/morph innerHTML of target
          - outer: replace outerHTML of target
          - prepend: insert before first child
          - append: insert after last child
          - before: insert before target element as sibling
          - after: insert after target element as sibling
          - remove: remove target element
        """
        lines = []

        # Datastar 1.0+ event format (https://data-star.dev/reference/sse_events#datastar-patch-elements)
        lines.append("event: datastar-patch-elements")
        if selector:
            lines.append(f"data: selector {selector}")
        if mode != "morph":
            lines.append(f"data: mode {mode}")
        if use_view_transition:
            lines.append("data: useViewTransition true")
        for line in elements.strip().splitlines():
            lines.append(f"data: elements {line}")
        lines.append("\n")

        # Datastar beta.9 backwards-compatibility event format
        lines.append("event: datastar-merge-fragments")
        if selector:
            lines.append(f"data: selector {selector}")
        if mode != "morph":
            lines.append(f"data: mergeMode {mode}")
        if settle_duration is not None:
            lines.append(f"data: settleDuration {settle_duration}")
        if use_view_transition:
            lines.append("data: useViewTransition true")
        for line in elements.strip().splitlines():
            lines.append(f"data: fragments {line}")
        lines.append("\n")

        return "\n".join(lines)

    @classmethod
    def merge_fragments(
        cls,
        html: str,
        selector: Optional[str] = None,
        merge_mode: str = "morph",
        settle_duration: Optional[int] = None,
        use_view_transition: bool = False,
    ) -> str:
        """
        Backwards-compatible alias for patch_elements.
        """
        return cls.patch_elements(
            elements=html,
            selector=selector,
            mode=merge_mode,
            settle_duration=settle_duration,
            use_view_transition=use_view_transition,
        )

    @classmethod
    def patch_signals(
        cls,
        signals: Dict[str, Any],
        only_if_missing: bool = False,
    ) -> str:
        """
        Creates an SSE frame to update reactive client-side signals in the Datastar store.
        (https://data-star.dev/reference/sse_events#datastar-patch-signals)
        """
        lines = []
        signals_json = json.dumps(signals)

        # Datastar 1.0+ event format
        lines.append("event: datastar-patch-signals")
        if only_if_missing:
            lines.append("data: onlyIfMissing true")
        lines.append(f"data: signals {signals_json}")
        lines.append("\n")

        # Datastar beta.9 backwards-compatibility event format
        lines.append("event: datastar-merge-signals")
        if only_if_missing:
            lines.append("data: onlyIfMissing true")
        lines.append(f"data: signals {signals_json}")
        lines.append("\n")

        return "\n".join(lines)

    @classmethod
    def merge_signals(
        cls,
        signals: Dict[str, Any],
        only_if_missing: bool = False,
    ) -> str:
        """
        Backwards-compatible alias for patch_signals.
        """
        return cls.patch_signals(signals=signals, only_if_missing=only_if_missing)

    @classmethod
    def execute_script(
        cls,
        script: str,
        auto_remove: bool = True,
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
