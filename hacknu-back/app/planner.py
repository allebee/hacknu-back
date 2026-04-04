"""
LLM planner — generates structured canvas operations.

Reads current storage state, rejected history, builds LLM prompt,
returns validated shape operations.
"""

from __future__ import annotations

import json
import logging
from openai import AsyncOpenAI

from app.config import AGENT_PROVIDER, AGENT_MODEL, OPENAI_API_KEY, GEMINI_API_KEY

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are an AI canvas assistant that generates shapes for a collaborative whiteboard.

Given the current canvas state and user context, generate shape operations.
Return a JSON object with:
{
  "operations": [
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:<unique_id>",
        "type": "<geo|arrow|note|text|frame>",
        "x": <number>,
        "y": <number>,
        "props": { ... type-specific props ... }
      }
    }
  ],
  "reasoning": "short explanation"
}

Shape types and their required props:
- geo: { geo, w, h, color, fill, dash, size, font, align, verticalAlign, richText, labelColor, scale }
  - geo values: rectangle, ellipse, triangle, diamond, pentagon, hexagon, octagon, star, cloud, heart
- arrow: { kind, start: {x,y}, end: {x,y}, bend, color, dash, size, arrowheadStart, arrowheadEnd, richText, scale }
- note: { color, size, font, align, verticalAlign, richText, scale }
- text: { color, size, font, textAlign, w, richText, scale, autoSize }
- frame: { w, h, name, color }

Color values: black, grey, light-violet, violet, blue, light-blue, yellow, orange, green, light-green, light-red, red, white
Fill values: none, semi, solid, pattern
Size values: s, m, l, xl
Font values: draw, sans, serif, mono

For richText, use Prosemirror format:
{ "type": "doc", "content": [{ "type": "paragraph", "content": [{ "type": "text", "text": "your text" }] }] }

Generate at most 20 shapes. Place shapes in visible viewport area.
Do NOT regenerate shapes that were previously rejected (listed in context).
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


def _build_context(
    storage: dict,
    rejected_ops: list[dict] | None = None,
    user_prompt: str = "",
    chat_history: list[dict] | None = None,
) -> str:
    """Build a compact context string from storage + history."""
    parts = []

    if user_prompt:
        parts.append(f"USER REQUEST: {user_prompt}")

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
            parts.append(
                f"  - [{stype}] id={sid} at ({s.get('x', 0):.0f},{s.get('y', 0):.0f})"
                + (f' text="{text}"' if text else "")
            )
    else:
        parts.append("CURRENT CANVAS: empty")

    # Rejected history
    if rejected_ops:
        parts.append("PREVIOUSLY REJECTED (DO NOT regenerate):")
        for rej in rejected_ops[-5:]:
            parts.append(f"  - {rej.get('reasoning', '?')}")

    # Chat history
    if chat_history:
        parts.append("RECENT CHAT CONTEXT:")
        for msg in chat_history[-10:]:
            parts.append(f"  [{msg['role']}]: {msg['content'][:200]}")

    return "\n".join(parts)


async def generate_operations(
    storage: dict,
    user_prompt: str = "",
    rejected_ops: list[dict] | None = None,
    chat_history: list[dict] | None = None,
) -> tuple[list[dict], str]:
    """
    Call LLM and return (operations_list, reasoning).
    """
    client, model = _get_client()
    context = _build_context(storage, rejected_ops, user_prompt, chat_history)

    logger.info(f"[Planner] model={model} context_len={len(context)}")

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            temperature=0.7,
            max_tokens=4096,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
    except Exception as e:
        logger.error(f"[Planner] LLM call failed: {e}")
        return [], f"LLM call failed: {e}"

    try:
        parsed = json.loads(raw)
        operations = parsed.get("operations", [])
        reasoning = parsed.get("reasoning", "")
        return operations, reasoning
    except json.JSONDecodeError as e:
        logger.error(f"[Planner] JSON parse failed: {e}")
        return [], "Failed to parse LLM response"


async def generate_query_answer(
    storage: dict,
    user_prompt: str,
    chat_history: list[dict] | None = None,
) -> tuple[str, list[str]]:
    """
    Answer a question about the canvas. Returns (answer, referenced_shape_ids).
    """
    client, model = _get_client()
    context = _build_context(storage, user_prompt=user_prompt, chat_history=chat_history)

    query_prompt = (
        "You are an AI canvas assistant. Answer the user's question about the current "
        "canvas content. Be concise. Return JSON: "
        '{"answer": "...", "referenced_shapes": ["shape:id1", ...]}'
    )

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": query_prompt},
                {"role": "user", "content": context},
            ],
            temperature=0.3,
            max_tokens=2048,
            response_format={"type": "json_object"},
        )
        raw = response.choices[0].message.content or "{}"
        parsed = json.loads(raw)
        return parsed.get("answer", ""), parsed.get("referenced_shapes", [])
    except Exception as e:
        logger.error(f"[Planner] Query failed: {e}")
        return f"Error: {e}", []
