"""
Agent Planner — core LLM integration with provider adapter pattern.

Builds context from the PlanRequest, selects the right prompt,
calls the LLM, and parses structured action output.
"""

from __future__ import annotations
import json
import os
import logging
from openai import AsyncOpenAI
from schemas import PlanRequest, PlanResponse, ShapeSummary
from prompts import MODE_PROMPTS
from validator import validate_actions
from memory import store

logger = logging.getLogger(__name__)

# ── Provider setup ────────────────────────────────────────────────────────────

def _get_client() -> tuple[AsyncOpenAI, str]:
    """Get OpenAI-compatible async client and model name."""
    provider = os.getenv("AGENT_PROVIDER", "openai").lower()
    model = os.getenv("AGENT_MODEL", "gpt-4o")

    if provider == "openai":
        client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    elif provider == "gemini" or provider == "google":
        # Use OpenAI-compatible endpoint for Gemini
        client = AsyncOpenAI(
            api_key=os.getenv("GEMINI_API_KEY") or os.getenv("GEMINI_API"),
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/"
        )
        if model == "gpt-4o":
            model = "gemini-2.0-flash"
    else:
        raise ValueError(f"Unknown provider: {provider}")

    return client, model


# ── Context builder ───────────────────────────────────────────────────────────

def _build_context(req: PlanRequest) -> str:
    """Build a compact context string from the plan request."""
    parts = []

    # User intent
    if req.user_intent:
        parts.append(f"USER INTENT: {req.user_intent}")

    # Mode
    parts.append(f"MODE: {req.mode}")

    # Viewport
    vp = req.viewport
    parts.append(f"VIEWPORT: center=({vp.x + vp.w/2:.0f}, {vp.y + vp.h/2:.0f}), "
                 f"size={vp.w:.0f}x{vp.h:.0f}, zoom={vp.zoom:.2f}")

    # Selected shapes
    if req.selection:
        parts.append("SELECTED SHAPES:")
        for s in req.selection[:10]:  # limit to 10
            parts.append(f"  - [{s.type}] id={s.id} text=\"{s.text}\" at ({s.x:.0f},{s.y:.0f}) {s.w:.0f}x{s.h:.0f}")

    # Visible shapes  
    if req.visible_shapes:
        parts.append(f"VISIBLE SHAPES ({len(req.visible_shapes)} total):")
        for s in req.visible_shapes[:20]:  # limit to 20
            parts.append(f"  - [{s.type}] id={s.id} text=\"{s.text}\" at ({s.x:.0f},{s.y:.0f}) {s.w:.0f}x{s.h:.0f}" +
                        (f" color={s.color}" if s.color else ""))
    else:
        parts.append("VISIBLE SHAPES: (empty board)")

    # Recent events
    if req.recent_events:
        parts.append("RECENT EVENTS:")
        for e in req.recent_events[:10]:
            parts.append(f"  - {e.type} [{e.shape_type}] id={e.shape_id} text=\"{e.text}\"")

    # Session history
    session = store.get_session(req.room_id)
    history_summary = session.get_summary(last_n=3)
    if "No previous" not in history_summary:
        parts.append(history_summary)

    # User controls
    parts.append(f"AUTONOMY: {req.autonomy_level}")
    if req.focus_scope:
        parts.append(f"FOCUS: {req.focus_scope}")

    return "\n".join(parts)


# ── Main planner function ─────────────────────────────────────────────────────

async def plan(req: PlanRequest) -> PlanResponse:
    """
    Main entry point: takes a PlanRequest, calls the LLM, returns validated actions.
    """
    client, model = _get_client()

    # Get mode-specific system prompt
    system_prompt = MODE_PROMPTS.get(req.mode)
    if not system_prompt:
        return PlanResponse(
            actions=[],
            reasoning=f"Unknown mode: {req.mode}",
            mode=req.mode,
        )

    # Build context
    context = _build_context(req)

    logger.info(f"[Planner] mode={req.mode} model={model} context_len={len(context)}")

    # Call LLM
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": context},
            ],
            temperature=0.7,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )

        raw_content = response.choices[0].message.content or "[]"
        logger.info(f"[Planner] raw response length: {len(raw_content)}")

    except Exception as e:
        logger.error(f"[Planner] LLM call failed: {e}")
        return PlanResponse(
            actions=[],
            reasoning=f"LLM call failed: {str(e)}",
            mode=req.mode,
        )

    # Parse JSON response
    try:
        parsed = json.loads(raw_content)

        # Handle both {"actions": [...]} and direct [...]
        if isinstance(parsed, dict):
            actions_raw = parsed.get("actions", [])
            reasoning = parsed.get("reasoning", "")
        elif isinstance(parsed, list):
            actions_raw = parsed
            reasoning = ""
        else:
            actions_raw = []
            reasoning = "Unexpected response format"

    except json.JSONDecodeError as e:
        logger.error(f"[Planner] JSON parse failed: {e}\nRaw: {raw_content[:500]}")
        return PlanResponse(
            actions=[],
            reasoning=f"Failed to parse LLM response as JSON",
            mode=req.mode,
        )

    # Validate and sanitize actions
    valid_actions, validation_errors = validate_actions(actions_raw)

    if validation_errors:
        logger.warning(f"[Planner] Validation errors: {validation_errors}")
        reasoning += " | Validation: " + "; ".join(validation_errors)

    # Resolve NEW_X references (cross-referencing new shapes)
    valid_actions = _resolve_new_references(valid_actions)

    # Store in session memory
    session = store.get_session(req.room_id)
    session.add_entry(
        mode=req.mode,
        user_intent=req.user_intent,
        actions=valid_actions,
        reasoning=reasoning,
    )

    return PlanResponse(
        actions=valid_actions,
        reasoning=reasoning,
        mode=req.mode,
        follow_up_delay_ms=_get_follow_up_delay(req.mode),
    )


def _resolve_new_references(actions: list[dict]) -> list[dict]:
    """
    Replace NEW_0, NEW_1, etc. references with the actual IDs
    of newly created shapes in the same batch.
    """
    # Map NEW_X → actual id for ALL create actions
    create_types = ("create_note", "create_shape", "create_text", "create_frame", "create_group")
    new_id_map: dict[str, str] = {}
    for i, action in enumerate(actions):
        action_type = action.get("action_type")
        if action_type in create_types:
            actual_id = action.get("id", "")
            new_id_map[f"NEW_{i}"] = actual_id

    # Replace references in all actions that reference shape IDs
    for action in actions:
        for key in ("from_id", "to_id", "shape_id"):
            val = action.get(key, "")
            if val in new_id_map:
                action[key] = new_id_map[val]
        # Also resolve within shape_ids arrays
        if "shape_ids" in action:
            action["shape_ids"] = [
                new_id_map.get(sid, sid) for sid in action["shape_ids"]
            ]

    return actions


def _get_follow_up_delay(mode: str) -> int:
    """Suggest a follow-up delay based on mode."""
    return {
        "ghostshape": 10000,   # check again in 10s
        "checkpoint": 0,       # one-shot
        "create": 0,           # one-shot
        "transform": 0,        # one-shot
    }.get(mode, 0)
