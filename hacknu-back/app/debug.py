"""
Shared debug-print helpers for tracing the AI flow end to end.
"""

from __future__ import annotations

import json
from typing import Any

from app.config import AI_DEBUG_PRINTS

_LLM_DEBUG_PREFIXES = (
    "planner.generate_operations.",
    "planner.generate_query_answer.",
    "transcript.summarize.",
)
_LLM_DEBUG_SUFFIXES = (
    ".system_prompt",
    ".user_context",
    ".user_input",
    ".llm_payload",
    ".llm_response",
    ".llm_raw_content",
    ".llm_error",
)


def _debug_default(value: Any) -> Any:
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return value.model_dump()
    if hasattr(value, "isoformat") and callable(value.isoformat):
        try:
            return value.isoformat()
        except TypeError:
            pass
    if isinstance(value, set):
        return sorted(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, Exception):
        return {"type": type(value).__name__, "message": str(value)}
    if hasattr(value, "__dict__"):
        return vars(value)
    return repr(value)


def _should_print_label(label: str) -> bool:
    return (
        label.startswith(_LLM_DEBUG_PREFIXES)
        and label.endswith(_LLM_DEBUG_SUFFIXES)
    )


def debug_print(label: str, payload: Any) -> None:
    if not AI_DEBUG_PRINTS or not _should_print_label(label):
        return

    print(f"\n[AI DEBUG] {label}", flush=True)
    if isinstance(payload, str):
        print(payload, flush=True)
    else:
        try:
            print(
                json.dumps(payload, ensure_ascii=False, indent=2, default=_debug_default),
                flush=True,
            )
        except Exception:
            print(repr(payload), flush=True)
    print(f"[AI DEBUG END] {label}\n", flush=True)
