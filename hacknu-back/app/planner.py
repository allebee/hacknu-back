"""
LLM planner — generates structured canvas operations.

Reads current storage state, rejected history, builds LLM prompt,
returns normalized shape operations.
"""

from __future__ import annotations

import json
import logging
from openai import AsyncOpenAI

from app.config import AGENT_PROVIDER, AGENT_MODEL, OPENAI_API_KEY, GEMINI_API_KEY
from app.debug import debug_print
from app.operations import AGENT_CONNECTION_META_KEY, compile_draft_operations, normalize_generated_operations

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
        "w": 200,
        "h": 100,
        "geo": "rectangle",
        "color": "blue",
        "fill": "solid",
        "size": "m",
        "font": "sans",
        "textAlign": "start",
        "align": "middle",
        "verticalAlign": "middle",
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
        "color": "green",
        "fill": "solid",
        "size": "m",
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
- If no canvas change is needed, return `"draft_operations": []`.
- Do NOT return full tldraw objects or low-level fields such as `id`, `index`, `parentId`, `isLocked`, `opacity`, `meta`, `props`, or raw arrow coordinates unless absolutely necessary.
- Use plain text in `label`; the backend will create rich text and other tldraw internals.
- For arrows and relationships, prefer `startShapeId` / `endShapeId` instead of geometry.
- To reference new shapes in the same response, put `"ref": "name"` on the `add_shape` and use `"ref:name"` in later operations.
- Use exact existing shape IDs from the canvas context when referring to current shapes.
- `x` / `y` are optional high-level layout hints, not precise geometry requirements.
- Keep suggestions minimal and useful. Do not recreate previously rejected ideas.
"""

CHAT_GENERATE_SYSTEM_PROMPT = f"""You are an AI canvas assistant for a collaborative whiteboard.
This mode serves explicit user chat requests.

Default to chat-only responses.
Do not create or modify canvas shapes unless the user is clearly asking for a visual or canvas change.
Use NO canvas changes for greetings, acknowledgements, small talk, plain questions, or ambiguous discussion.
If the user confirms a previously discussed canvas action with messages like "do it", "yes", or "go ahead", treat that as approval and proceed.
If a necessary detail is missing, ask a concise clarifying question in `reasoning` and return no draft operations.
When a canvas change is needed, make the minimum useful change and prefer updating existing shapes over adding redundant ones.

{GENERATION_OUTPUT_CONTRACT}
"""

AUTOCOMPLETE_SYSTEM_PROMPT = f"""You are an AI autocomplete assistant for a collaborative whiteboard.
This mode is proactive, but not pushy.

Look at the current board state and meeting context and suggest one cohesive, useful improvement when there is a clear opportunity.
Prefer small suggestions: a missing note, a connection, a label, a light grouping frame, or a short flow continuation.
If nothing meaningful should be added right now, return no draft operations.
Avoid speculative or decorative additions.
Prefer at most 6 new shapes unless the context strongly supports a slightly larger structure.

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


def _generation_prompt_for_mode(request_mode: str) -> tuple[str, float]:
    if request_mode == "autocomplete":
        return AUTOCOMPLETE_SYSTEM_PROMPT, 0.7
    if request_mode == "edit_suggestion":
        return EDIT_SUGGESTION_SYSTEM_PROMPT, 0.45
    return CHAT_GENERATE_SYSTEM_PROMPT, 0.5


def _build_context(
    storage: dict,
    rejected_ops: list[dict] | None = None,
    user_prompt: str = "",
    chat_history: list[dict] | None = None,
    meeting_context: str | None = None,
    request_mode: str = "chat_generate",
    include_full_storage: bool = False,
) -> str:
    """Build a compact context string from storage + history + meeting."""
    parts = [f"REQUEST MODE: {request_mode}"]

    if user_prompt:
        parts.append(f"USER REQUEST: {user_prompt}")

    # Meeting context (from Google Meet transcript)
    if meeting_context:
        parts.append(meeting_context)

    # Current shapes on canvas
    shapes = storage.get("shapes", {})
    if shapes:
        parts.append(f"CURRENT CANVAS ({len(shapes)} shapes):")
        for sid, s in list(shapes.items())[:30]:
            stype = s.get("type", "?")
            text = ""
            props = s.get("props", {})
            rt = props.get("richText", {})
            if isinstance(rt, dict):
                for c in rt.get("content", []):
                    for t in c.get("content", []):
                        text += t.get("text", "")
            # Build a rich description with dimensions and color
            desc = f"  - [{stype}] id={sid} at ({s.get('x', 0):.0f},{s.get('y', 0):.0f})"
            w = props.get("w")
            h = props.get("h")
            if w is not None and h is not None:
                desc += f" size={w}x{h}"
            elif w is not None:
                desc += f" w={w}"
            color = props.get("color")
            if color:
                desc += f" color={color}"
            geo = props.get("geo")
            if geo and stype == "geo":
                desc += f" geo={geo}"
            # Arrow connection info
            if stype == "arrow":
                start = props.get("start", {})
                end = props.get("end", {})
                desc += f" from=({start.get('x', 0):.0f},{start.get('y', 0):.0f}) to=({end.get('x', 0):.0f},{end.get('y', 0):.0f})"
                meta = s.get("meta", {})
                if isinstance(meta, dict):
                    connection = meta.get(AGENT_CONNECTION_META_KEY)
                    if isinstance(connection, dict):
                        start_shape_id = connection.get("startShapeId")
                        end_shape_id = connection.get("endShapeId")
                        if start_shape_id and end_shape_id:
                            desc += f" links={start_shape_id}->{end_shape_id}"
            if text:
                desc += f' text="{text[:80]}"'
            parts.append(desc)
    else:
        parts.append("CURRENT CANVAS: empty")

    if include_full_storage:
        parts.append("FULL CANVAS STORAGE JSON:")
        parts.append(json.dumps(storage, ensure_ascii=True, separators=(",", ":")))

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
        parsed = json.loads(raw)
        debug_print("planner.generate_operations.parsed_json", parsed)
        draft_operations = parsed.get("draft_operations")
        if draft_operations is None:
            draft_operations = parsed.get("operations", [])
        debug_print("planner.generate_operations.draft_operations", draft_operations)
        compiled = compile_draft_operations(storage, draft_operations)
        debug_print("planner.generate_operations.compiled_operations", compiled)
        operations = normalize_generated_operations(storage, compiled)
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
    )

    query_prompt = (
        "You are an AI canvas assistant. Answer the user's question about the current "
        "canvas content. Be concise. Return JSON: "
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
        parsed = json.loads(raw)
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
