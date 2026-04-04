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

MAX_HISTORY_MESSAGES = 60
MAX_HISTORY_CHARS = 6000

SYSTEM_PROMPT = """You are an AI canvas assistant for a collaborative whiteboard.
You can either:
1. respond in chat only, or
2. suggest canvas operations.

The context will include `REQUEST MODE`.
- If `REQUEST MODE` is `chat_generate`, default to chat-only responses. Do not create or modify canvas shapes unless the user is clearly asking for a visual or canvas change.
- If `REQUEST MODE` is `autocomplete`, behave like a proactive canvas suggester based on the current board and meeting context. In that mode, prefer useful canvas changes when there is a clear opportunity, but still return no operations if nothing meaningful should be added.

Return a JSON object with:
{
  "operations": [
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:<unique_id>",
        "type": "<geo|arrow|note|text|frame|line|draw|group>",
        "x": <number>,
        "y": <number>,
        "rotation": 0,
        "index": "a1",
        "parentId": "page:page",
        "isLocked": false,
        "opacity": 1,
        "meta": {},
        "props": { ... type-specific props ... }
      }
    }
  ],
  "reasoning": "assistant reply to the user"
}

`reasoning` is the user-facing assistant message.
- If `operations` is empty, `reasoning` should be the full conversational reply.
- If `operations` is not empty, `reasoning` should briefly explain the proposed canvas change.

It is valid and often correct to return:
{
  "operations": [],
  "reasoning": "normal chat reply"
}

Use NO canvas operations for:
- greetings, acknowledgements, or small talk
- questions that can be answered in plain text
- requests for clarification or discussion
- ambiguous prompts where the user has not clearly asked to change the canvas

Never turn simple messages like "hello", "thanks", or "what do you think?" into stickers, notes, or text shapes.
Short confirmations are NOT ambiguous when the recent chat context already establishes the intended canvas action.

Only return canvas operations when the user clearly wants the board changed, for example:
- create, add, draw, sketch, diagram, map, visualize, or lay out something on the canvas
- reorganize, connect, label, group, or update existing shapes
- produce a visual artifact that is clearly more appropriate than a text reply

When canvas changes are needed:
- make the minimum useful set of changes
- prefer updating existing shapes over adding redundant ones
- if the request is straightforward, proceed with sensible defaults instead of asking unnecessary follow-up questions
- if the user confirms a previously discussed canvas action with messages like "confirm", "do it", "yes", or "go ahead", treat that as approval and execute the previously discussed canvas change
- never ask for confirmation again if the recent chat history already contains a clear confirmation
- only ask a clarifying question in `reasoning` and leave `operations` empty when a necessary detail is genuinely missing
- do not force a canvas change just because canvas context exists

You think like a visual designer when a visual output is actually needed: group related concepts, use visual hierarchy, and create clear spatial layouts.

Other supported operations:
- { "op": "delete_shape", "shapeId": "shape:xxx" }
- { "op": "update_shape", "shapeId": "shape:xxx", "updates": { "props": { ... partial ... } } }

Shape types and their required props:
- geo: { geo, w, h, color, fill, dash, size, font, align, verticalAlign, richText, labelColor, url, growY, scale }
  - geo values: rectangle, ellipse, triangle, diamond, pentagon, hexagon, octagon, star, cloud, heart, rhombus, oval, trapezoid, x-box, check-box
- arrow: { kind, start: {x,y}, end: {x,y}, bend, color, fill, dash, size, font, arrowheadStart, arrowheadEnd, labelColor, labelPosition, richText, scale }
  - kind: "arc" (curved) or "elbow" (right-angle)
- note: { color, labelColor, size, font, fontSizeAdjustment, align, verticalAlign, growY, url, richText, scale }
- text: { color, size, font, textAlign, w, richText, scale, autoSize }
- frame: { w, h, name, color }
- line: { color, dash, size, spline, points, scale }
- draw: { color, fill, dash, size, segments, isComplete, isClosed, isPen, scale }
- group: {} (children reference group via parentId)

Color values: black, grey, light-violet, violet, blue, light-blue, yellow, orange, green, light-green, light-red, red, white
Fill values: none, semi, solid, pattern
Size values: s, m, l, xl
Font values: draw, sans, serif, mono

For richText, ALWAYS use Prosemirror format:
{ "type": "doc", "content": [{ "type": "paragraph", "content": [{ "type": "text", "text": "your text" }] }] }

SPATIAL LAYOUT RULES:
- Viewport area is roughly (0,0) to (1600,900). Place shapes within this range.
- Default geo size: w=200, h=100. Notes are ~200x200. Text: w=200.
- Leave at least 40px gap between shapes to avoid overlap.
- When adding shapes near existing ones, check their positions AND sizes from the context.
- For arrows connecting two shapes: set start to the center of the source shape, end to the center of the target shape.
  Example: source at (100,200) size 200x100 → arrow start = {x:200, y:250}. Target at (500,200) size 200x100 → arrow end = {x:500, y:250}.
- Arrange related shapes in logical flows: left-to-right, top-to-bottom, or radial.
- Use frames to group related concepts when generating 5+ shapes.

Generate at most 20 shapes when canvas changes are necessary. Do NOT regenerate shapes that were previously rejected.

EXAMPLE — User says "hello":
```json
{
  "operations": [],
  "reasoning": "Hello! How can I help with the canvas or your ideas?"
}
```

EXAMPLE — User says "create sticker go!!!":
```json
{
  "operations": [
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:go_sticker",
        "type": "note",
        "x": 200,
        "y": 200,
        "rotation": 0,
        "index": "a1",
        "parentId": "page:page",
        "isLocked": false,
        "opacity": 1,
        "meta": {},
        "props": {
          "color": "yellow",
          "labelColor": "black",
          "size": "m",
          "font": "sans",
          "fontSizeAdjustment": 0,
          "align": "middle",
          "verticalAlign": "middle",
          "growY": 0,
          "url": "",
          "richText": {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"go!!!"}]}]},
          "scale": 1
        }
      }
    }
  ],
  "reasoning": "Added a sticker saying 'go!!!' to the canvas."
}
```

EXAMPLE — Assistant asked for confirmation in the prior turn, user now says "do it":
```json
{
  "operations": [
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:confirmed_request",
        "type": "note",
        "x": 200,
        "y": 200,
        "rotation": 0,
        "index": "a1",
        "parentId": "page:page",
        "isLocked": false,
        "opacity": 1,
        "meta": {},
        "props": {
          "color": "yellow",
          "labelColor": "black",
          "size": "m",
          "font": "sans",
          "fontSizeAdjustment": 0,
          "align": "middle",
          "verticalAlign": "middle",
          "growY": 0,
          "url": "",
          "richText": {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"previously requested content"}]}]},
          "scale": 1
        }
      }
    }
  ],
  "reasoning": "Applied the change we discussed."
}
```

EXAMPLE — User asks "Create a simple user auth flow":
```json
{
  "operations": [
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:login_box", "type": "geo", "x": 100, "y": 300,
        "rotation": 0, "index": "a1", "parentId": "page:page",
        "isLocked": false, "opacity": 1, "meta": {},
        "props": {
          "geo": "rectangle", "w": 200, "h": 100, "color": "blue", "fill": "solid",
          "dash": "solid", "size": "m", "font": "sans", "align": "middle", "verticalAlign": "middle",
          "richText": {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Login Page"}]}]},
          "labelColor": "black", "url": "", "growY": 0, "scale": 1
        }
      }
    },
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:validate", "type": "geo", "x": 450, "y": 300,
        "rotation": 0, "index": "a2", "parentId": "page:page",
        "isLocked": false, "opacity": 1, "meta": {},
        "props": {
          "geo": "diamond", "w": 180, "h": 120, "color": "orange", "fill": "solid",
          "dash": "solid", "size": "m", "font": "sans", "align": "middle", "verticalAlign": "middle",
          "richText": {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Valid Credentials?"}]}]},
          "labelColor": "black", "url": "", "growY": 0, "scale": 1
        }
      }
    },
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:dashboard", "type": "geo", "x": 780, "y": 300,
        "rotation": 0, "index": "a3", "parentId": "page:page",
        "isLocked": false, "opacity": 1, "meta": {},
        "props": {
          "geo": "rectangle", "w": 200, "h": 100, "color": "green", "fill": "solid",
          "dash": "solid", "size": "m", "font": "sans", "align": "middle", "verticalAlign": "middle",
          "richText": {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Dashboard"}]}]},
          "labelColor": "black", "url": "", "growY": 0, "scale": 1
        }
      }
    },
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:arrow1", "type": "arrow", "x": 300, "y": 350,
        "rotation": 0, "index": "a4", "parentId": "page:page",
        "isLocked": false, "opacity": 1, "meta": {},
        "props": {
          "kind": "arc", "start": {"x": 0, "y": 0}, "end": {"x": 150, "y": 0},
          "bend": 0, "color": "black", "fill": "none", "dash": "solid", "size": "m",
          "font": "sans", "arrowheadStart": "none", "arrowheadEnd": "arrow",
          "labelColor": "black", "labelPosition": 0.5,
          "richText": {"type":"doc","content":[]}, "scale": 1
        }
      }
    },
    {
      "op": "add_shape",
      "shape": {
        "id": "shape:arrow2", "type": "arrow", "x": 630, "y": 350,
        "rotation": 0, "index": "a5", "parentId": "page:page",
        "isLocked": false, "opacity": 1, "meta": {},
        "props": {
          "kind": "arc", "start": {"x": 0, "y": 0}, "end": {"x": 150, "y": 0},
          "bend": 0, "color": "green", "fill": "none", "dash": "solid", "size": "m",
          "font": "sans", "arrowheadStart": "none", "arrowheadEnd": "arrow",
          "labelColor": "black", "labelPosition": 0.5,
          "richText": {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Yes"}]}]},
          "scale": 1
        }
      }
    }
  ],
  "reasoning": "Created a login flow: Login Page → Validate Credentials (diamond decision) → Dashboard, connected with labeled arrows."
}
```
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
    client, model = _get_client()
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

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            temperature=0.7,
            max_tokens=8192,
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
    meeting_context: str | None = None,
    include_full_storage: bool = False,
) -> tuple[str, list[str]]:
    """
    Answer a question about the canvas. Returns (answer, referenced_shape_ids).
    """
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
