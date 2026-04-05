"""
LLM planner — generates structured canvas operations.

Reads current storage state, rejected history, builds LLM prompt,
returns normalized shape operations.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
from openai import AsyncOpenAI

from app.config import AGENT_PROVIDER, AGENT_MODEL, OPENAI_API_KEY, GEMINI_API_KEY
from app.debug import debug_print
from app.operations import (
    AGENT_CONNECTION_META_KEY,
    compile_draft_operations,
    normalize_generated_operations,
    normalize_viewport,
)

logger = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 60
MAX_HISTORY_CHARS = 6000

GENERATION_OUTPUT_CONTRACT = """
Return JSON only:
{
  "reasoning": "assistant reply",
  "draft_operations": [
    {
      "op": "add_shape",
      "ref": "optional_local_name",
      "shape": {
        "type": "geo|note|text|frame|arrow",
        "label": "plain text shown on the shape",
        "x": 100,
        "y": 200,
        "w": 260,
        "h": 140,
        "geo": "rectangle",
        "name": "frame title",
        "nearShapeId": "shape:existing_or_ref:new_box",
        "placement": "right|left|above|below|inside",
        "startShapeId": "shape:existing_or_ref:new_box",
        "endShapeId": "shape:existing_or_ref:new_box"
      }
    },
    {
      "op": "update_shape",
      "shapeId": "shape:existing_or_ref:new_box",
      "updates": {
        "label": "new text",
        "geo": "diamond",
        "w": 220,
        "h": 120,
        "startShapeId": "shape:a",
        "endShapeId": "shape:b"
      }
    },
    { "op": "delete_shape", "shapeId": "shape:existing_or_ref:new_box" }
  ]
}

Rules:
- `reasoning` is the user-facing assistant message.
- `reasoning` should sound natural from the user's perspective. Unless the user explicitly asks for raw canvas structure, do not mention exact `x` / `y`, `w` / `h`, shape IDs, viewport bounds, or exhaustive shape counts.
- If no canvas change is needed, return `"draft_operations": []`.
- Only include keys relevant to the chosen shape type.
- `note` add/update: `label` plus optional `x` / `y`.
- `text` add/update: `label` plus optional `x` / `y`.
- `geo` add/update: `geo`, optional `label`, optional `x` / `y`, and only include `w` / `h` when size materially affects structure.
- `frame` add/update: `name`, `x`, `y`, `w`, `h`.
- `arrow` add/update: optional `label`, plus `startShapeId` / `endShapeId`. Only use raw `start` / `end` if there is no meaningful shape connection.
- Before adding an arrow with `startShapeId` / `endShapeId`, check the current canvas for an existing arrow between the same two shapes in either direction.
- Allow at most one arrow between any two shape IDs.
- If an arrow already exists from `A -> B`, do not create `B -> A` as a separate back arrow. Keep the existing `A -> B` arrow and update its label text only when needed.
- If that arrow already exists, do not create a duplicate arrow between the same two shape IDs. Reuse or update the existing arrow only when the user is asking to change that relationship.
- When existing comparable items already follow a clear linkage pattern, preserve that same linkage logic for any newly added significant item instead of inventing a different relationship.
- If an existing sequence or chain of notes, text, or geo shapes is connected by arrows, include the matching arrow connection for the newly added significant shape in the same response unless the user explicitly asks for a different structure.
- More generally, continue the current relationship mechanism for similar items when it is clear from the canvas, such as arrow links, frame containment, or a repeated aligned sequence.
- Do NOT return full tldraw objects or low-level fields such as `id`, `index`, `parentId`, `isLocked`, `opacity`, `meta`, `props`, or raw arrow coordinates unless absolutely necessary.
- Use plain text in `label`; the backend will create rich text and other tldraw internals.
- Do not send style fields such as `font`, `color`, `fill`, `dash`, `size`, `labelColor`, `align`, `verticalAlign`, `textAlign`, `arrowheadStart`, or `arrowheadEnd`; the backend hardcodes them.
- For notes and text, only send the text plus optional `x` / `y`.
- For geo shapes, send `geo`, text, optional `x` / `y`, and only include `w` / `h` when size materially affects the structure.
- For frames, send `name`, `x`, `y`, `w`, and `h`.
- For arrows and relationships, prefer `startShapeId` / `endShapeId` instead of geometry.
- To reference new shapes in the same response, put `"ref": "name"` on the `add_shape` and use `"ref:name"` in later operations.
- Use exact existing shape IDs from the canvas context when referring to current shapes.
- When a shape includes `eventTs`, treat larger / newer timestamps as more recent edits or additions.
- When extending a sequence, cluster, or connected chain, anchor the new shape or change to the newest relevant shape by `eventTs`, not the oldest one.
- If several connected stickers or notes form a row or flow, place the next one adjacent to the latest sticker or note in that chain.
- `x` / `y` are optional high-level layout hints, not precise geometry requirements.
- Place every new shape in a clean row-or-column layout relative to the current canvas: align it by exact `x` or exact `y` with an existing shape, or with a new shape already added earlier in the same response.
- Never place a new shape diagonally relative to its nearest related shape. If it goes left or right, keep the same `y`; if it goes above or below, keep the same `x`.
- When `CURRENT VIEWPORT` is present in the user context, treat it as the visible canvas rectangle in canvas coordinates.
- Keep every new non-arrow shape fully inside the current viewport unless the user explicitly asks to extend beyond it or the related target shape is already outside it.
- Prefer filling open space inside the current viewport before starting a new row or column.
- Keep suggestions minimal and useful. Do not recreate previously rejected ideas.
"""

CHAT_GENERATE_SYSTEM_PROMPT = f"""You are an AI canvas assistant for a collaborative whiteboard.
This mode serves explicit user chat requests.

Default to chat-only responses.
Do not create or modify canvas shapes unless the user is clearly asking for a visual or canvas change.
Use NO canvas changes for greetings, acknowledgements, small talk, plain questions, or ambiguous discussion.
If the user confirms a previously discussed canvas action with messages like "do it", "yes", or "go ahead", treat that as approval and proceed.
If a necessary detail is missing, ask a concise clarifying question in `reasoning` and return no draft operations.
If the user asks to explain, summarize, describe, review, or walk through the canvas or project, answer at a high level for a human reader.
Focus on the goal, major sections, flow, and notable decisions instead of narrating raw canvas primitives.
Do not list every note, arrow, frame, or text box unless the user explicitly asks for an inventory.
Do not mention exact coordinates, dimensions, shape IDs, or exact item counts unless the user explicitly asks for those details.
When layout matters, use relative language like "top area", "left side", "main flow", or "grouped together" instead of raw geometry.
When a canvas change is needed, make the minimum useful change and prefer updating existing shapes over adding redundant ones.
Return either no draft operations or exactly 1 significant non-arrow canvas change.
Do not use a standalone arrow, a tiny cosmetic tweak, or a label-only nudge as that significant change.
Supporting connector arrows or small linkage-preserving updates are allowed only when they are needed to support that one significant non-arrow change.

{GENERATION_OUTPUT_CONTRACT}
"""

AUTOCOMPLETE_SYSTEM_PROMPT = f"""You are an AI autocomplete assistant for a collaborative whiteboard.
This mode is proactive, but not pushy.

Look at the current board state and meeting context and suggest one cohesive, useful improvement when there is a clear opportunity.
Prefer small suggestions: a missing note, a connection, a label, a light grouping frame, or a short flow continuation.
If nothing meaningful should be added right now, return no draft operations.
Avoid speculative or decorative additions.
Return either no draft operations or exactly 1 significant non-arrow canvas change.
Do not make standalone arrow-only, tiny cosmetic, or label-only suggestions.
Connector arrows or other purely linking support are allowed only when they support that one significant non-arrow change.
Prefer at most 3 new shapes total unless the context strongly supports a slightly larger structure around that one significant change.

{GENERATION_OUTPUT_CONTRACT}
"""

EDIT_SUGGESTION_SYSTEM_PROMPT = f"""You are revising a previous pending canvas suggestion.
This mode is for editing an existing suggestion after user feedback.

Prefer adjusting the previous intent instead of discarding it.
Be decisive when the user's edit request has a sensible interpretation.
Only return no draft operations if the correct outcome is genuinely to make no canvas change.
Favor modifying or reconnecting existing suggested content instead of starting over.

{GENERATION_OUTPUT_CONTRACT}
"""


def _get_client() -> tuple[AsyncOpenAI, str]:
    """Get OpenAI-compatible async client and model name."""
    provider = AGENT_PROVIDER
    model = AGENT_MODEL

    if provider == "openai":
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
    elif provider in ("gemini", "google"):
        client = AsyncOpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        if model == "gpt-4o":
            model = "gemini-2.0-flash"
    else:
        raise ValueError(f"Unknown provider: {provider}")

    return client, model


def _chat_completion_token_limit_kwargs(limit: int) -> dict[str, int]:
    """
    Build the correct token-limit parameter for the configured provider.

    OpenAI's newer reasoning/chat models reject `max_tokens` and require
    `max_completion_tokens`. Gemini's OpenAI-compatible endpoint still expects
    the legacy field.
    """
    if AGENT_PROVIDER == "openai":
        return {"max_completion_tokens": limit}
    return {"max_tokens": limit}


def _strip_wrapping_code_fence(raw: str) -> str:
    text = raw.strip()
    if not text.startswith("```"):
        return text

    lines = text.splitlines()
    if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
        return "\n".join(lines[1:-1]).strip()
    return text


def _parse_json_object_response(raw: str, *, context_label: str) -> dict:
    text = _strip_wrapping_code_fence(raw)
    if not text:
        return {}

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as original_error:
        decoder = json.JSONDecoder()
        parsed_values: list[object] = []
        idx = 0

        while idx < len(text):
            while idx < len(text) and text[idx].isspace():
                idx += 1
            if idx >= len(text):
                break

            if text[idx] not in "{[":
                next_positions = [
                    pos for pos in (text.find("{", idx), text.find("[", idx)) if pos != -1
                ]
                if not next_positions:
                    break
                idx = min(next_positions)

            try:
                value, idx = decoder.raw_decode(text, idx)
            except json.JSONDecodeError:
                break
            parsed_values.append(value)

        parsed_objects = [value for value in parsed_values if isinstance(value, dict)]
        if not parsed_objects:
            raise original_error

        if len(parsed_objects) > 1:
            if all(obj == parsed_objects[0] for obj in parsed_objects[1:]):
                logger.warning(
                    "[Planner] %s returned %s duplicate JSON objects; using the first one",
                    context_label,
                    len(parsed_objects),
                )
                return parsed_objects[0]

            logger.warning(
                "[Planner] %s returned %s JSON objects; using the last one",
                context_label,
                len(parsed_objects),
            )
            return parsed_objects[-1]

        trailing = text[idx:].strip()
        if trailing:
            logger.warning(
                "[Planner] %s returned malformed JSON with trailing content; using the first JSON object",
                context_label,
            )
        else:
            logger.warning(
                "[Planner] %s returned malformed JSON; salvaged the first JSON object",
                context_label,
            )
        return parsed_objects[0]

    if not isinstance(parsed, dict):
        raise json.JSONDecodeError("Expected top-level JSON object", text, 0)
    return parsed


def _generation_prompt_for_mode(request_mode: str) -> tuple[str, float]:
    if request_mode == "autocomplete":
        return AUTOCOMPLETE_SYSTEM_PROMPT, 0.7
    if request_mode == "edit_suggestion":
        return EDIT_SUGGESTION_SYSTEM_PROMPT, 0.45
    return CHAT_GENERATE_SYSTEM_PROMPT, 0.5


def _format_viewport_for_context(viewport: object) -> str | None:
    viewport_bounds = normalize_viewport(viewport)
    if viewport_bounds is None:
        return None

    parts = [
        f"x={viewport_bounds.x:.0f}",
        f"y={viewport_bounds.y:.0f}",
        f"width={viewport_bounds.width:.0f}",
        f"height={viewport_bounds.height:.0f}",
        f"right={viewport_bounds.x + viewport_bounds.width:.0f}",
        f"bottom={viewport_bounds.y + viewport_bounds.height:.0f}",
    ]
    if viewport_bounds.zoom is not None:
        parts.append(f"zoom={viewport_bounds.zoom:.2f}")
    return "CURRENT VIEWPORT: " + ", ".join(parts)


def _rich_text_to_plain_text(rich_text: object) -> str:
    if not isinstance(rich_text, dict):
        return ""

    parts: list[str] = []
    for block in rich_text.get("content", []):
        if not isinstance(block, dict):
            continue
        for node in block.get("content", []):
            if isinstance(node, dict):
                text = node.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _shape_text(shape: dict) -> str:
    props = shape.get("props")
    if isinstance(props, dict):
        text = _rich_text_to_plain_text(props.get("richText"))
        if text:
            return text
    for key in ("text", "label", "name"):
        value = shape.get(key)
        if isinstance(value, str):
            return value
    return ""


def _normalize_event_ts(value: object) -> tuple[float | None, str | None]:
    if isinstance(value, bool):
        return None, None

    if isinstance(value, (int, float)):
        timestamp = float(value)
        if abs(timestamp) > 1e11:
            timestamp /= 1000.0
        try:
            dt = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None, None
        return dt.timestamp(), dt.isoformat().replace("+00:00", "Z")

    if not isinstance(value, str):
        return None, None

    raw = value.strip()
    if not raw:
        return None, None

    try:
        return _normalize_event_ts(float(raw))
    except ValueError:
        pass

    iso_candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        dt = datetime.fromisoformat(iso_candidate)
    except ValueError:
        return None, None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.timestamp(), dt.isoformat().replace("+00:00", "Z")


def _shape_event_ts(shape: dict) -> tuple[float | None, str | None]:
    meta = shape.get("meta")
    if not isinstance(meta, dict):
        return None, None
    return _normalize_event_ts(meta.get("event_ts"))


def _sorted_shapes_for_llm(shapes: object) -> list[tuple[str, dict]]:
    if not isinstance(shapes, dict):
        return []

    shape_items = [
        (shape_id, shape)
        for shape_id, shape in shapes.items()
        if isinstance(shape_id, str) and isinstance(shape, dict)
    ]

    def _sort_key(item: tuple[str, dict]) -> tuple[bool, float]:
        event_ts_value, _ = _shape_event_ts(item[1])
        return event_ts_value is not None, event_ts_value or 0.0

    return sorted(
        shape_items,
        key=_sort_key,
        reverse=True,
    )


def _compact_shape_for_llm(shape_id: str, shape: dict) -> dict | None:
    shape_type = shape.get("type", "?")
    if shape_type in {"draw", "group"}:
        return None

    compact = {
        "id": shape_id,
        "type": shape_type,
        "x": shape.get("x", 0),
        "y": shape.get("y", 0),
    }
    _, event_ts = _shape_event_ts(shape)
    if event_ts is not None:
        compact["eventTs"] = event_ts
    props = shape.get("props")
    props = props if isinstance(props, dict) else {}
    text = _shape_text(shape)

    if shape_type == "note":
        compact["text"] = text
        return compact

    if shape_type == "geo":
        compact["geo"] = props.get("geo")
        compact["w"] = props.get("w")
        compact["h"] = props.get("h")
        if text:
            compact["text"] = text
        return {key: value for key, value in compact.items() if value is not None}

    if shape_type == "text":
        compact["text"] = text
        return {key: value for key, value in compact.items() if value is not None}

    if shape_type == "frame":
        compact["w"] = props.get("w")
        compact["h"] = props.get("h")
        compact["name"] = props.get("name")
        return {key: value for key, value in compact.items() if value is not None}

    if shape_type == "arrow":
        meta = shape.get("meta")
        meta = meta if isinstance(meta, dict) else {}
        connection = meta.get(AGENT_CONNECTION_META_KEY)
        if isinstance(connection, dict):
            compact["startShapeId"] = connection.get("startShapeId")
            compact["endShapeId"] = connection.get("endShapeId")
        else:
            compact["start"] = props.get("start")
            compact["end"] = props.get("end")
        if text:
            compact["text"] = text
        return {key: value for key, value in compact.items() if value is not None}

    if shape_type == "line":
        points = props.get("points")
        if isinstance(points, dict):
            compact["pointCount"] = len(points)
        return {key: value for key, value in compact.items() if value is not None}

    return compact


def _compact_updates_for_llm(updates: dict) -> dict:
    compact: dict = {}
    for key in (
        "x",
        "y",
        "w",
        "h",
        "geo",
        "name",
        "startShapeId",
        "endShapeId",
    ):
        if key in updates:
            compact[key] = updates[key]

    text = ""
    props = updates.get("props")
    if isinstance(props, dict):
        text = _rich_text_to_plain_text(props.get("richText"))
        for key in (
            "w",
            "h",
            "name",
            "start",
            "end",
        ):
            if key in props and key not in compact:
                compact[key] = props[key]

    if not text:
        for key in ("text", "label", "name"):
            value = updates.get(key)
            if isinstance(value, str):
                text = value
                break
    if text:
        compact["text"] = text

    return compact


def _compact_operation_for_llm(operation: dict) -> dict:
    compact = {"op": operation.get("op")}
    if "shapeId" in operation:
        compact["shapeId"] = operation.get("shapeId")

    shape = operation.get("shape")
    if isinstance(shape, dict):
        shape_id = shape.get("id")
        compact_shape = _compact_shape_for_llm(shape_id if isinstance(shape_id, str) else "", shape)
        if compact_shape is not None:
            compact["shape"] = compact_shape

    updates = operation.get("updates")
    if isinstance(updates, dict):
        compact["updates"] = _compact_updates_for_llm(updates)

    return compact


def _compact_storage_for_llm(storage: dict) -> dict:
    compact: dict = {}

    sorted_shapes = _sorted_shapes_for_llm(storage.get("shapes"))
    if sorted_shapes:
        compact_shapes: dict[str, dict] = {}
        for shape_id, shape in sorted_shapes:
            compact_shape = _compact_shape_for_llm(shape_id, shape)
            if compact_shape is not None:
                compact_shapes[shape_id] = compact_shape
        if compact_shapes:
            compact["shapes"] = compact_shapes

    pending_changes = storage.get("pendingChanges")
    if isinstance(pending_changes, dict):
        compact["pendingChanges"] = {}
        for change_id, change in pending_changes.items():
            if not isinstance(change_id, str) or not isinstance(change, dict):
                continue
            compact_change = {
                "id": change.get("id", change_id),
                "agentId": change.get("agentId"),
                "status": change.get("status"),
                "reasoning": change.get("reasoning"),
            }
            operations = change.get("operations")
            if isinstance(operations, list):
                compact_ops = [
                    _compact_operation_for_llm(op)
                    for op in operations
                    if isinstance(op, dict)
                ]
                compact_ops = [
                    op for op in compact_ops
                    if op.get("shape") is not None or op.get("updates") is not None or op.get("shapeId") is not None
                ]
                if compact_ops:
                    compact_change["operations"] = compact_ops
            compact["pendingChanges"][change_id] = compact_change

    return compact


def _describe_shape_for_llm(shape_id: str, shape: dict) -> str | None:
    compact = _compact_shape_for_llm(shape_id, shape)
    if compact is None:
        return None
    shape_type = compact.get("type", "?")
    desc = f"  - [{shape_type}] id={shape_id} at ({compact.get('x', 0):.0f},{compact.get('y', 0):.0f})"
    event_ts = compact.get("eventTs")
    if isinstance(event_ts, str) and event_ts:
        desc += f" eventTs={event_ts}"

    if shape_type == "note":
        text = compact.get("text")
        if isinstance(text, str) and text:
            desc += f' text="{text[:80]}"'
        return desc

    if shape_type == "geo":
        if compact.get("geo"):
            desc += f" geo={compact['geo']}"
        if compact.get("w") is not None and compact.get("h") is not None:
            desc += f" size={compact['w']}x{compact['h']}"
    elif shape_type == "text":
        pass
    elif shape_type == "frame":
        if compact.get("w") is not None and compact.get("h") is not None:
            desc += f" size={compact['w']}x{compact['h']}"
        if compact.get("name"):
            desc += f' name="{compact["name"]}"'
    elif shape_type == "arrow":
        start_shape_id = compact.get("startShapeId")
        end_shape_id = compact.get("endShapeId")
        if start_shape_id and end_shape_id:
            desc += f" links={start_shape_id}->{end_shape_id}"
        elif compact.get("start") is not None and compact.get("end") is not None:
            desc += f" from={compact['start']} to={compact['end']}"
    elif shape_type == "line":
        point_count = compact.get("pointCount")
        if point_count is not None:
            desc += f" points={point_count}"

    text = compact.get("text")
    if isinstance(text, str) and text:
        desc += f' text="{text[:80]}"'

    return desc


def _build_context(
    storage: dict,
    rejected_ops: list[dict] | None = None,
    user_prompt: str = "",
    chat_history: list[dict] | None = None,
    meeting_context: str | None = None,
    request_mode: str = "chat_generate",
    include_full_storage: bool = False,
    viewport: object | None = None,
) -> str:
    """Build a compact context string from storage + history + meeting."""
    parts = [f"REQUEST MODE: {request_mode}"]

    if user_prompt:
        parts.append(f"USER REQUEST: {user_prompt}")

    viewport_context = _format_viewport_for_context(viewport)
    if viewport_context:
        parts.append(viewport_context)

    # Meeting context (from Google Meet transcript)
    if meeting_context:
        parts.append(meeting_context)

    # Current shapes on canvas
    visible_shapes: list[str] = []
    for sid, s in _sorted_shapes_for_llm(storage.get("shapes"))[:30]:
        desc = _describe_shape_for_llm(sid, s)
        if desc:
            visible_shapes.append(desc)

    if visible_shapes:
        parts.append(f"CURRENT CANVAS ({len(visible_shapes)} shapes):")
        parts.extend(visible_shapes)
    else:
        parts.append("CURRENT CANVAS: empty")

    if include_full_storage:
        parts.append("COMPACT CANVAS STORAGE JSON:")
        parts.append(
            json.dumps(
                _compact_storage_for_llm(storage),
                ensure_ascii=True,
                separators=(",", ":"),
            )
        )

    # Rejected history — include both reasoning and what shapes were rejected
    if rejected_ops:
        parts.append("PREVIOUSLY REJECTED (DO NOT regenerate these or similar):")
        for rej in rejected_ops[-5:]:
            rej_desc = rej.get('reasoning', '?')
            rej_ops = rej.get('operations', [])
            if rej_ops and isinstance(rej_ops, list):
                shape_types = [op.get('shape', {}).get('type', '?') for op in rej_ops if isinstance(op, dict) and op.get('shape')]
                if shape_types:
                    rej_desc += f" (shapes: {', '.join(shape_types)})"
            parts.append(f"  - {rej_desc}")

    # Chat history
    if chat_history:
        parts.append("RECENT CHAT CONTEXT:")
        total_chars = 0
        selected_messages: list[str] = []
        for msg in reversed(chat_history[-MAX_HISTORY_MESSAGES:]):
            role = msg.get("role", "assistant")
            msg_type = msg.get("type", "text")
            if msg_type == "change":
                status = msg.get("change_status", "pending")
                summary = (msg.get("operations_summary") or "")[:240]
                content = f"[change:{status}] {summary}".strip()
            else:
                content = (msg.get("content") or "")[:500]

            line = f"  [{role}]: {content}"
            if selected_messages and total_chars + len(line) > MAX_HISTORY_CHARS:
                break
            selected_messages.append(line)
            total_chars += len(line)

        for line in reversed(selected_messages):
            parts.append(line)

    return "\n".join(parts)


async def generate_operations(
    storage: dict,
    user_prompt: str = "",
    rejected_ops: list[dict] | None = None,
    chat_history: list[dict] | None = None,
    meeting_context: str | None = None,
    request_mode: str = "chat_generate",
    include_full_storage: bool = False,
    viewport: object | None = None,
) -> tuple[list[dict], str]:
    """
    Call LLM and return (operations_list, reasoning).
    """
    debug_print(
        "planner.generate_operations.received",
        {
            "user_prompt": user_prompt,
            "rejected_ops": rejected_ops,
            "chat_history": chat_history,
            "meeting_context": meeting_context,
            "request_mode": request_mode,
            "include_full_storage": include_full_storage,
            "viewport": viewport,
            "storage": storage,
        },
    )

    client, model = _get_client()
    system_prompt, temperature = _generation_prompt_for_mode(request_mode)
    context = _build_context(
        storage,
        rejected_ops,
        user_prompt,
        chat_history,
        meeting_context,
        request_mode,
        include_full_storage,
        viewport,
    )

    logger.info(f"[Planner] model={model} context_len={len(context)}")
    debug_print("planner.generate_operations.system_prompt", system_prompt)
    debug_print("planner.generate_operations.user_context", context)

    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ],
        "temperature": temperature,
        **_chat_completion_token_limit_kwargs(8192),
        "response_format": {"type": "json_object"},
    }
    debug_print("planner.generate_operations.llm_payload", request_payload)

    try:
        response = await client.chat.completions.create(**request_payload)
        debug_print("planner.generate_operations.llm_response", response)
        raw = response.choices[0].message.content or "{}"
        debug_print("planner.generate_operations.llm_raw_content", raw)
    except Exception as e:
        logger.error(f"[Planner] LLM call failed: {e}")
        debug_print("planner.generate_operations.llm_error", e)
        return [], f"LLM call failed: {e}"

    try:
        parsed = _parse_json_object_response(raw, context_label="generate_operations")
        debug_print("planner.generate_operations.parsed_json", parsed)
        draft_operations = parsed.get("draft_operations")
        if draft_operations is None:
            draft_operations = parsed.get("operations", [])
        debug_print("planner.generate_operations.draft_operations", draft_operations)
        compiled = compile_draft_operations(storage, draft_operations, viewport=viewport)
        debug_print("planner.generate_operations.compiled_operations", compiled)
        operations = normalize_generated_operations(storage, compiled, viewport=viewport)
        reasoning = str(parsed.get("reasoning") or parsed.get("reply") or "")
        debug_print(
            "planner.generate_operations.return_value",
            {
                "reasoning": reasoning,
                "operations": operations,
            },
        )
        return operations, reasoning
    except json.JSONDecodeError as e:
        logger.error(f"[Planner] JSON parse failed: {e}")
        debug_print(
            "planner.generate_operations.parse_error",
            {
                "error": e,
                "raw_content": raw,
            },
        )
        return [], "Failed to parse LLM response"
    except Exception as e:
        logger.error(f"[Planner] Operation normalization failed: {e}")
        debug_print(
            "planner.generate_operations.normalization_error",
            {
                "error": e,
                "raw_content": raw,
            },
        )
        return [], "Failed to normalize generated canvas operations"


async def generate_query_answer(
    storage: dict,
    user_prompt: str,
    chat_history: list[dict] | None = None,
    meeting_context: str | None = None,
    include_full_storage: bool = False,
    viewport: object | None = None,
) -> tuple[str, list[str]]:
    """
    Answer a question about the canvas. Returns (answer, referenced_shape_ids).
    """
    debug_print(
        "planner.generate_query_answer.received",
        {
            "user_prompt": user_prompt,
            "chat_history": chat_history,
            "meeting_context": meeting_context,
            "include_full_storage": include_full_storage,
            "viewport": viewport,
            "storage": storage,
        },
    )

    client, model = _get_client()
    context = _build_context(
        storage,
        user_prompt=user_prompt,
        chat_history=chat_history,
        meeting_context=meeting_context,
        include_full_storage=include_full_storage,
        viewport=viewport,
    )

    query_prompt = (
        "You are an AI canvas assistant. Answer the user's question about the current "
        "canvas content. Be concise and answer at the user's level of abstraction. "
        "For explanation or summary requests, focus on intent, structure, and major relationships. "
        "Do not mention exact coordinates, dimensions, shape IDs, or exact item counts unless the user explicitly asks. "
        "Return JSON: "
        '{"answer": "...", "referenced_shapes": ["shape:id1", ...]}'
    )
    debug_print("planner.generate_query_answer.system_prompt", query_prompt)
    debug_print("planner.generate_query_answer.user_context", context)

    request_payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": query_prompt},
            {"role": "user", "content": context},
        ],
        "temperature": 0.3,
        **_chat_completion_token_limit_kwargs(2048),
        "response_format": {"type": "json_object"},
    }
    debug_print("planner.generate_query_answer.llm_payload", request_payload)

    try:
        response = await client.chat.completions.create(**request_payload)
        debug_print("planner.generate_query_answer.llm_response", response)
        raw = response.choices[0].message.content or "{}"
        debug_print("planner.generate_query_answer.llm_raw_content", raw)
        parsed = _parse_json_object_response(raw, context_label="generate_query_answer")
        debug_print("planner.generate_query_answer.parsed_json", parsed)
        answer = parsed.get("answer", "")
        refs = parsed.get("referenced_shapes", [])
        debug_print(
            "planner.generate_query_answer.return_value",
            {
                "answer": answer,
                "referenced_shapes": refs,
            },
        )
        return answer, refs
    except Exception as e:
        logger.error(f"[Planner] Query failed: {e}")
        debug_print("planner.generate_query_answer.error", e)
        return f"Error: {e}", []
