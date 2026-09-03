import time
import threading
from typing import Dict, Any, Optional


class TaskTelemetryContext:
    """
    Thread-local context manager and registry for tracking runtime telemetry,
    execution duration, and LLM token metrics during background task execution.
    """
    _thread_local = threading.local()

    def __init__(self):
        self.start_time: float = 0.0
        self.end_time: float = 0.0
        self.duration_ms: float = 0.0
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.total_tokens: int = 0
        self.llm_calls_count: int = 0
        self.llm_duration_ms: float = 0.0
        self.extra_metrics: Dict[str, Any] = {}
        self._previous_context: Optional["TaskTelemetryContext"] = None

    def __enter__(self) -> "TaskTelemetryContext":
        self._previous_context = getattr(self._thread_local, "current", None)
        self._thread_local.current = self
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.perf_counter()
        self.duration_ms = max(0.0, (self.end_time - self.start_time) * 1000.0)
        self.total_tokens = self.input_tokens + self.output_tokens
        self._thread_local.current = self._previous_context

    @classmethod
    def get_current(cls) -> Optional["TaskTelemetryContext"]:
        """Returns the currently active telemetry context in this thread, if any."""
        return getattr(cls._thread_local, "current", None)

    def record_generation(
        self,
        input_tokens: int = 0,
        output_tokens: int = 0,
        duration_ms: Optional[float] = None,
        tokens_per_second: Optional[float] = None,
    ):
        """Records token metrics from an LLM generation call."""
        self.input_tokens += max(0, int(input_tokens or 0))
        self.output_tokens += max(0, int(output_tokens or 0))
        self.total_tokens = self.input_tokens + self.output_tokens
        self.llm_calls_count += 1
        if duration_ms is not None:
            self.llm_duration_ms += max(0.0, float(duration_ms))
        if tokens_per_second is not None:
            self.extra_metrics["last_tps"] = round(float(tokens_per_second), 2)

    def record_metric(self, key: str, value: Any):
        """Records an arbitrary custom task metric."""
        self.extra_metrics[key] = value

    def to_dict(self) -> Dict[str, Any]:
        """Serializes telemetry into a JSON-compatible dictionary."""
        data = {
            "duration_ms": round(self.duration_ms, 2),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "llm_calls_count": self.llm_calls_count,
            "llm_duration_ms": round(self.llm_duration_ms, 2),
        }
        if self.extra_metrics:
            data["extra"] = self.extra_metrics
        return data


def record_task_generation(
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: Optional[float] = None,
    tokens_per_second: Optional[float] = None,
):
    """
    Convenience function for LLM service / external callers to record tokens
    into the active task telemetry context if one exists.
    """
    ctx = TaskTelemetryContext.get_current()
    if ctx:
        ctx.record_generation(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            tokens_per_second=tokens_per_second,
        )
