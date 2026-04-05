"""
Operation normalization for agent-generated canvas changes.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
import re
import uuid

from pydantic import ValidationError

from app.schemas import ShapeOperation
from app.shapes import ArrowShape, FrameShape, GeoShape, NoteShape, SHAPE_TYPE_MAP, TextShape

AGENT_CONNECTION_META_KEY = "agentConnection"
MIN_SHAPE_GAP = 40.0
DEFAULT_NOTE_WIDTH = 200.0
DEFAULT_NOTE_HEIGHT = 200.0
DEFAULT_TEXT_WIDTH = 200.0
DEFAULT_TEXT_HEIGHT = 60.0
MAX_CONNECTION_ENDPOINT_DISTANCE = 320.0
DEFAULT_INSERT_X = 120.0
DEFAULT_INSERT_Y = 120.0
DEFAULT_AI_FONT = "sans"
DEFAULT_AI_LABEL_COLOR = "black"
ARROW_PROP_KEYS = {
    "start",
    "end",
    "bend",
    "color",
    "fill",
    "dash",
    "size",
    "font",
    "kind",
    "arrowheadStart",
    "arrowheadEnd",
    "labelColor",
    "labelPosition",
    "richText",
    "scale",
    "elbowMidPoint",
}


@dataclass
class Bounds:
    x: float
    y: float
    w: float
    h: float


@dataclass
class ConnectionSpec:
    start_shape_id: str
    end_shape_id: str
    start_anchor: dict[str, float]
    end_anchor: dict[str, float]


@dataclass
class IndexState:
    next_number: int
    used: set[str]


def compile_draft_operations(storage: dict, operations: object) -> list[dict]:
    """Compile high-level draft operations into concrete tldraw operations."""
    raw_ops = [op for op in operations if isinstance(op, dict)] if isinstance(operations, list) else []
    current_shapes = _storage_shapes(storage)
    virtual_shapes = deepcopy(current_shapes)
    ref_map = _build_ref_map(raw_ops, virtual_shapes)
    index_state = _build_index_state(virtual_shapes)
    compiled: list[dict] = []

    for op_data in raw_ops:
        compiled_op = _compile_draft_operation(op_data, virtual_shapes, ref_map, index_state)
        if compiled_op is None:
            continue
        compiled.append(compiled_op)
        _apply_virtual_operation(virtual_shapes, compiled_op)

    return compiled


def _compile_draft_operation(
    op_data: dict,
    virtual_shapes: dict[str, dict],
    ref_map: dict[str, str],
    index_state: IndexState,
) -> dict | None:
    op_name = op_data.get("op")

    if op_name == "add_shape":
        shape = op_data.get("shape")
        if not isinstance(shape, dict):
            return None
        if _looks_like_concrete_shape(shape):
            compiled_shape = _hydrate_legacy_shape(shape, op_data, virtual_shapes, ref_map, index_state)
        else:
            compiled_shape = _compile_semantic_add_shape(shape, op_data, virtual_shapes, ref_map, index_state)
        if compiled_shape is None:
            return None
        return {"op": "add_shape", "shape": compiled_shape}

    if op_name == "update_shape":
        raw_shape_id = op_data.get("shapeId")
        if not isinstance(raw_shape_id, str):
            return None
        shape_id = _resolve_shape_reference(raw_shape_id, ref_map)
        updates = op_data.get("updates")
        if not isinstance(shape_id, str) or not shape_id or not isinstance(updates, dict):
            return None

        current_shape = virtual_shapes.get(shape_id)
        if not isinstance(current_shape, dict):
            return None

        compiled_updates = _compile_draft_updates(current_shape, updates, ref_map)
        if not compiled_updates:
            return None
        return {
            "op": "update_shape",
            "shapeId": shape_id,
            "updates": compiled_updates,
        }

    if op_name == "delete_shape":
        raw_shape_id = op_data.get("shapeId")
        if not isinstance(raw_shape_id, str):
            return None
        shape_id = _resolve_shape_reference(raw_shape_id, ref_map)
        if not isinstance(shape_id, str) or not shape_id:
            return None
        return {"op": "delete_shape", "shapeId": shape_id}

    return None


def _build_ref_map(raw_ops: list[dict], virtual_shapes: dict[str, dict]) -> dict[str, str]:
    used_ids = {
        shape_id
        for shape_id, shape in virtual_shapes.items()
        if isinstance(shape_id, str) and isinstance(shape, dict)
    }
    ref_map: dict[str, str] = {}

    for op_data in raw_ops:
        if op_data.get("op") != "add_shape":
            continue
        shape = op_data.get("shape")
        if not isinstance(shape, dict):
            continue
        ref = _draft_ref(op_data, shape)
        if not ref:
            continue

        shape_id = shape.get("id")
        if isinstance(shape_id, str) and shape_id:
            used_ids.add(shape_id)
            ref_map[ref] = shape_id
            continue

        ref_map[ref] = _allocate_shape_id(used_ids, ref)

    return ref_map


def _build_index_state(virtual_shapes: dict[str, dict]) -> IndexState:
    used: set[str] = set()
    max_numeric = 0

    for shape in virtual_shapes.values():
        if not isinstance(shape, dict):
            continue
        index = shape.get("index")
        if not isinstance(index, str) or not index:
            continue
        used.add(index)
        match = re.fullmatch(r"a(\d+)", index)
        if match:
            max_numeric = max(max_numeric, int(match.group(1)))

    next_number = max_numeric + 1 if max_numeric else max(len(used) + 1, 1)
    return IndexState(next_number=next_number, used=used)


def _looks_like_concrete_shape(shape: dict) -> bool:
    return any(
        key in shape
        for key in ("id", "props", "meta", "index", "parentId", "rotation", "opacity", "isLocked")
    )


def _hydrate_legacy_shape(
    shape: dict,
    op_data: dict,
    virtual_shapes: dict[str, dict],
    ref_map: dict[str, str],
    index_state: IndexState,
) -> dict:
    hydrated = deepcopy(shape)
    ref = _draft_ref(op_data, shape)
    hydrated.pop("ref", None)
    shape_id = hydrated.get("id")
    if not isinstance(shape_id, str) or not shape_id:
        hydrated["id"] = ref_map.get(ref) if ref else None
    if not isinstance(hydrated.get("id"), str) or not hydrated["id"]:
        hydrated["id"] = _allocate_shape_id(set(virtual_shapes) | set(ref_map.values()), ref)

    hydrated.setdefault("x", DEFAULT_INSERT_X)
    hydrated.setdefault("y", DEFAULT_INSERT_Y)
    hydrated.setdefault("rotation", 0)
    hydrated.setdefault("index", _allocate_index(index_state))
    hydrated.setdefault("parentId", "page:page")
    hydrated.setdefault("isLocked", False)
    hydrated.setdefault("opacity", 1.0)
    hydrated.setdefault("meta", {})
    return hydrated


def _compile_semantic_add_shape(
    shape: dict,
    op_data: dict,
    virtual_shapes: dict[str, dict],
    ref_map: dict[str, str],
    index_state: IndexState,
) -> dict | None:
    shape_type = shape.get("type")
    if not isinstance(shape_type, str):
        return None

    ref = _draft_ref(op_data, shape)
    shape_id = ref_map.get(ref) if ref else None
    if not shape_id:
        shape_id = _allocate_shape_id(set(virtual_shapes) | set(ref_map.values()), ref)

    if shape_type == "geo":
        x, y = _shape_position(shape, virtual_shapes, ref_map, shape_type)
        compiled = GeoShape(id=shape_id, x=x, y=y, index=_allocate_index(index_state)).model_dump(exclude_none=True)
        props = compiled["props"]
        props["font"] = DEFAULT_AI_FONT
        props["labelColor"] = DEFAULT_AI_LABEL_COLOR
        _set_geo_defaults_from_draft(props, shape)
        return compiled

    if shape_type == "note":
        x, y = _shape_position(shape, virtual_shapes, ref_map, shape_type)
        compiled = NoteShape(id=shape_id, x=x, y=y, index=_allocate_index(index_state)).model_dump(exclude_none=True)
        props = compiled["props"]
        props["font"] = DEFAULT_AI_FONT
        props["labelColor"] = DEFAULT_AI_LABEL_COLOR
        _set_note_defaults_from_draft(props, shape)
        return compiled

    if shape_type == "text":
        x, y = _shape_position(shape, virtual_shapes, ref_map, shape_type)
        compiled = TextShape(id=shape_id, x=x, y=y, index=_allocate_index(index_state)).model_dump(exclude_none=True)
        props = compiled["props"]
        props["font"] = DEFAULT_AI_FONT
        _set_text_defaults_from_draft(props, shape)
        return compiled

    if shape_type == "frame":
        x, y = _shape_position(shape, virtual_shapes, ref_map, shape_type)
        compiled = FrameShape(id=shape_id, x=x, y=y, index=_allocate_index(index_state)).model_dump(exclude_none=True)
        props = compiled["props"]
        _set_frame_defaults_from_draft(props, shape)
        return compiled

    if shape_type == "arrow":
        compiled = ArrowShape(id=shape_id, x=0, y=0, index=_allocate_index(index_state)).model_dump(exclude_none=True)
        props = compiled["props"]
        props["font"] = DEFAULT_AI_FONT
        props["labelColor"] = DEFAULT_AI_LABEL_COLOR
        _set_arrow_defaults_from_draft(compiled, shape, ref_map)
        return compiled

    return None


def _compile_draft_updates(current_shape: dict, updates: dict, ref_map: dict[str, str]) -> dict:
    compiled: dict = {}
    for key in ("x", "y", "rotation", "index", "parentId", "isLocked", "opacity"):
        if key in updates:
            compiled[key] = deepcopy(updates[key])

    if isinstance(updates.get("props"), dict):
        compiled["props"] = deepcopy(updates["props"])
    if isinstance(updates.get("meta"), dict):
        compiled["meta"] = deepcopy(updates["meta"])

    shape_type = current_shape.get("type")
    props = compiled.setdefault("props", {})
    if not isinstance(props, dict):
        props = {}
        compiled["props"] = props

    if shape_type == "geo":
        _apply_geo_update_semantics(props, updates)
    elif shape_type == "note":
        _apply_note_update_semantics(props, updates)
    elif shape_type == "text":
        _apply_text_update_semantics(props, updates)
    elif shape_type == "frame":
        _apply_frame_update_semantics(props, updates)
    elif shape_type == "arrow":
        _apply_arrow_update_semantics(compiled, props, updates, ref_map)

    if "props" in compiled and not compiled["props"]:
        compiled.pop("props")
    if "meta" in compiled and not compiled["meta"]:
        compiled.pop("meta")
    return compiled


def _draft_ref(op_data: dict, shape: dict) -> str | None:
    for raw in (op_data.get("ref"), shape.get("ref")):
        if isinstance(raw, str):
            value = raw.strip()
            if value:
                return value
    return None


def _allocate_shape_id(used_ids: set[str], ref: str | None = None) -> str:
    slug = _slugify(ref or "")
    if slug:
        candidate = f"shape:{slug}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate

    while True:
        candidate = f"shape:{uuid.uuid4().hex[:12]}"
        if candidate not in used_ids:
            used_ids.add(candidate)
            return candidate


def _allocate_index(index_state: IndexState) -> str:
    while True:
        candidate = f"a{index_state.next_number}"
        index_state.next_number += 1
        if candidate not in index_state.used:
            index_state.used.add(candidate)
            return candidate


def _shape_position(
    shape: dict,
    virtual_shapes: dict[str, dict],
    ref_map: dict[str, str],
    shape_type: str,
) -> tuple[float, float]:
    explicit_x = shape.get("x")
    explicit_y = shape.get("y")
    if isinstance(explicit_x, (int, float)) and isinstance(explicit_y, (int, float)):
        return float(explicit_x), float(explicit_y)

    width, height = _draft_shape_dimensions(shape_type, shape)
    near_shape_id = _resolve_shape_reference(shape.get("nearShapeId"), ref_map)
    placement = shape.get("placement")
    if isinstance(near_shape_id, str):
        near_shape = virtual_shapes.get(near_shape_id)
        if isinstance(near_shape, dict):
            x, y = _position_near_shape(near_shape, width, height, placement)
            if isinstance(explicit_x, (int, float)):
                x = float(explicit_x)
            if isinstance(explicit_y, (int, float)):
                y = float(explicit_y)
            return x, y

    default_x, default_y = _default_insert_position(virtual_shapes, width, height)
    if isinstance(explicit_x, (int, float)):
        default_x = float(explicit_x)
    if isinstance(explicit_y, (int, float)):
        default_y = float(explicit_y)
    return default_x, default_y


def _draft_shape_dimensions(shape_type: str, shape: dict) -> tuple[float, float]:
    if shape_type == "note":
        return DEFAULT_NOTE_WIDTH, DEFAULT_NOTE_HEIGHT
    if shape_type == "text":
        return max(_as_float(shape.get("w"), DEFAULT_TEXT_WIDTH), 1.0), DEFAULT_TEXT_HEIGHT
    if shape_type == "frame":
        return max(_as_float(shape.get("w"), 400.0), 1.0), max(_as_float(shape.get("h"), 300.0), 1.0)
    return max(_as_float(shape.get("w"), 200.0), 1.0), max(_as_float(shape.get("h"), 100.0), 1.0)


def _position_near_shape(near_shape: dict, width: float, height: float, placement: object) -> tuple[float, float]:
    bounds = _shape_bounds(near_shape)
    if bounds is None:
        center = _shape_center(near_shape)
        return center["x"], center["y"]

    direction = placement.strip().lower() if isinstance(placement, str) else "right"
    if direction == "left":
        return max(0.0, bounds.x - width - MIN_SHAPE_GAP), max(0.0, bounds.y + (bounds.h - height) / 2)
    if direction == "above":
        return max(0.0, bounds.x + (bounds.w - width) / 2), max(0.0, bounds.y - height - MIN_SHAPE_GAP)
    if direction == "below":
        return max(0.0, bounds.x + (bounds.w - width) / 2), bounds.y + bounds.h + MIN_SHAPE_GAP
    if direction == "inside":
        return bounds.x + MIN_SHAPE_GAP, bounds.y + MIN_SHAPE_GAP
    return bounds.x + bounds.w + MIN_SHAPE_GAP, max(0.0, bounds.y + (bounds.h - height) / 2)


def _default_insert_position(virtual_shapes: dict[str, dict], width: float, height: float) -> tuple[float, float]:
    occupied = _occupied_bounds(virtual_shapes)
    if not occupied:
        return DEFAULT_INSERT_X, DEFAULT_INSERT_Y

    max_right = max(item.x + item.w for item in occupied)
    min_y = min(item.y for item in occupied)
    x = max_right + MIN_SHAPE_GAP
    y = min_y
    if x + width > 1520:
        x = DEFAULT_INSERT_X
        y = max(item.y + item.h for item in occupied) + MIN_SHAPE_GAP
    return x, y


def _set_geo_defaults_from_draft(props: dict, shape: dict) -> None:
    props["geo"] = _string_choice(shape.get("geo"), props["geo"])
    props["w"] = max(_as_float(shape.get("w"), props["w"]), 1.0)
    props["h"] = max(_as_float(shape.get("h"), props["h"]), 1.0)
    _apply_common_style(props, shape, include_fill=True)
    props["align"] = _string_choice(shape.get("align"), props["align"])
    props["verticalAlign"] = _string_choice(shape.get("verticalAlign"), props["verticalAlign"])
    props["richText"] = _rich_text_from_shape(shape)


def _set_note_defaults_from_draft(props: dict, shape: dict) -> None:
    _apply_common_style(props, shape, include_fill=False)
    props["align"] = _string_choice(shape.get("align"), props["align"])
    props["verticalAlign"] = _string_choice(shape.get("verticalAlign"), props["verticalAlign"])
    props["richText"] = _rich_text_from_shape(shape)


def _set_text_defaults_from_draft(props: dict, shape: dict) -> None:
    props["color"] = _string_choice(shape.get("color"), props["color"])
    props["size"] = _string_choice(shape.get("size"), props["size"])
    props["font"] = _string_choice(shape.get("font"), props["font"])
    props["textAlign"] = _string_choice(shape.get("textAlign"), props["textAlign"])
    props["w"] = max(_as_float(shape.get("w"), props["w"]), 1.0)
    if isinstance(shape.get("autoSize"), bool):
        props["autoSize"] = shape["autoSize"]
    props["richText"] = _rich_text_from_shape(shape)


def _set_frame_defaults_from_draft(props: dict, shape: dict) -> None:
    props["w"] = max(_as_float(shape.get("w"), props["w"]), 1.0)
    props["h"] = max(_as_float(shape.get("h"), props["h"]), 1.0)
    props["color"] = _string_choice(shape.get("color"), props["color"])
    if isinstance(shape.get("name"), str):
        props["name"] = shape["name"]


def _set_arrow_defaults_from_draft(compiled: dict, shape: dict, ref_map: dict[str, str]) -> None:
    props = compiled["props"]
    _apply_common_style(props, shape, include_fill=True)
    props["kind"] = _string_choice(shape.get("kind"), props["kind"])
    props["arrowheadStart"] = _string_choice(shape.get("arrowheadStart"), props["arrowheadStart"])
    props["arrowheadEnd"] = _string_choice(shape.get("arrowheadEnd"), props["arrowheadEnd"])
    label = _shape_label(shape)
    if label:
        props["richText"] = _rich_text(label)

    start_shape_id = _resolve_shape_reference(shape.get("startShapeId"), ref_map)
    end_shape_id = _resolve_shape_reference(shape.get("endShapeId"), ref_map)
    if isinstance(start_shape_id, str) and isinstance(end_shape_id, str) and start_shape_id != end_shape_id:
        compiled["meta"][AGENT_CONNECTION_META_KEY] = {
            "startShapeId": start_shape_id,
            "endShapeId": end_shape_id,
            "startAnchor": {"x": 0.5, "y": 0.5},
            "endAnchor": {"x": 0.5, "y": 0.5},
        }
    else:
        if isinstance(shape.get("x"), (int, float)):
            compiled["x"] = float(shape["x"])
        if isinstance(shape.get("y"), (int, float)):
            compiled["y"] = float(shape["y"])
        if isinstance(shape.get("start"), dict):
            props["start"] = _point(shape["start"])
        if isinstance(shape.get("end"), dict):
            props["end"] = _point(shape["end"])


def _apply_common_style(props: dict, source: dict, *, include_fill: bool) -> None:
    props["color"] = _string_choice(source.get("color"), props.get("color", "black"))
    if include_fill and "fill" in props:
        props["fill"] = _string_choice(source.get("fill"), props["fill"])
    if "dash" in props:
        props["dash"] = _string_choice(source.get("dash"), props["dash"])
    props["size"] = _string_choice(source.get("size"), props.get("size", "m"))
    props["font"] = _string_choice(source.get("font"), props.get("font", DEFAULT_AI_FONT))
    if "labelColor" in props:
        props["labelColor"] = _string_choice(source.get("labelColor"), props["labelColor"])


def _apply_geo_update_semantics(props: dict, updates: dict) -> None:
    if "geo" in updates:
        props["geo"] = updates["geo"]
    if "w" in updates:
        props["w"] = max(_as_float(updates["w"]), 1.0)
    if "h" in updates:
        props["h"] = max(_as_float(updates["h"]), 1.0)
    for key in ("color", "fill", "dash", "size", "font", "align", "verticalAlign", "labelColor"):
        if key in updates:
            props[key] = updates[key]
    label = _shape_label(updates)
    if label is not None:
        props["richText"] = _rich_text(label)


def _apply_note_update_semantics(props: dict, updates: dict) -> None:
    for key in ("color", "size", "font", "align", "verticalAlign", "labelColor"):
        if key in updates:
            props[key] = updates[key]
    label = _shape_label(updates)
    if label is not None:
        props["richText"] = _rich_text(label)


def _apply_text_update_semantics(props: dict, updates: dict) -> None:
    for key in ("color", "size", "font", "textAlign"):
        if key in updates:
            props[key] = updates[key]
    if "w" in updates:
        props["w"] = max(_as_float(updates["w"]), 1.0)
    if "autoSize" in updates and isinstance(updates["autoSize"], bool):
        props["autoSize"] = updates["autoSize"]
    label = _shape_label(updates)
    if label is not None:
        props["richText"] = _rich_text(label)


def _apply_frame_update_semantics(props: dict, updates: dict) -> None:
    for key in ("color", "name"):
        if key in updates:
            props[key] = updates[key]
    if "w" in updates:
        props["w"] = max(_as_float(updates["w"]), 1.0)
    if "h" in updates:
        props["h"] = max(_as_float(updates["h"]), 1.0)


def _apply_arrow_update_semantics(compiled: dict, props: dict, updates: dict, ref_map: dict[str, str]) -> None:
    for key in (
        "color",
        "fill",
        "dash",
        "size",
        "font",
        "kind",
        "arrowheadStart",
        "arrowheadEnd",
        "labelColor",
        "labelPosition",
    ):
        if key in updates:
            props[key] = updates[key]
    if "start" in updates:
        props["start"] = _point(updates["start"])
    if "end" in updates:
        props["end"] = _point(updates["end"])
    label = _shape_label(updates)
    if label is not None:
        props["richText"] = _rich_text(label)

    start_shape_id = _resolve_shape_reference(updates.get("startShapeId"), ref_map)
    end_shape_id = _resolve_shape_reference(updates.get("endShapeId"), ref_map)
    if isinstance(start_shape_id, str) and isinstance(end_shape_id, str) and start_shape_id != end_shape_id:
        meta = compiled.setdefault("meta", {})
        meta[AGENT_CONNECTION_META_KEY] = {
            "startShapeId": start_shape_id,
            "endShapeId": end_shape_id,
            "startAnchor": {"x": 0.5, "y": 0.5},
            "endAnchor": {"x": 0.5, "y": 0.5},
        }


def _shape_label(shape: dict) -> str | None:
    for key in ("label", "text", "name"):
        value = shape.get(key)
        if isinstance(value, str):
            return value
    return None


def _rich_text_from_shape(shape: dict) -> dict:
    label = _shape_label(shape) or ""
    return _rich_text(label)


def _rich_text(text: str) -> dict:
    paragraph: dict = {"type": "paragraph"}
    if text:
        paragraph["content"] = [{"type": "text", "text": text}]
    return {"type": "doc", "content": [paragraph]}


def _resolve_shape_reference(value: object, ref_map: dict[str, str]) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    if raw.startswith("shape:"):
        return raw
    if raw.startswith("ref:"):
        return ref_map.get(raw[4:].strip())
    return ref_map.get(raw, raw)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.lower()).strip("_")
    return slug[:40]


def _string_choice(value: object, default: str) -> str:
    if isinstance(value, str) and value:
        return value
    return default


def normalize_generated_operations(storage: dict, operations: object) -> list[dict]:
    """Validate and normalize newly generated operations before storing them."""
    raw_ops = [op for op in operations if isinstance(op, dict)] if isinstance(operations, list) else []
    current_shapes = _storage_shapes(storage)
    virtual_shapes = deepcopy(current_shapes)
    normalized_ops: list[dict] = []
    deferred_arrow_ops: list[dict] = []
    occupied = _occupied_bounds(virtual_shapes)

    for op_data in raw_ops:
        op_name = op_data.get("op")
        if op_name == "add_shape":
            normalized = _normalize_add_shape(op_data, virtual_shapes, occupied, defer_arrows=True)
            if normalized is None:
                continue
            if _is_arrow_add(normalized):
                deferred_arrow_ops.append(normalized)
                continue
            normalized_ops.append(normalized)
            _apply_virtual_operation(virtual_shapes, normalized)
            occupied = _occupied_bounds(virtual_shapes)
            continue

        if op_name == "update_shape":
            if _targets_arrow(op_data, virtual_shapes):
                deferred_arrow_ops.append(deepcopy(op_data))
                continue
            normalized = _normalize_update_shape(op_data, virtual_shapes)
            if normalized is None:
                continue
            normalized_ops.append(normalized)
            _apply_virtual_operation(virtual_shapes, normalized)
            occupied = _occupied_bounds(virtual_shapes)
            continue

        if op_name == "delete_shape":
            shape_id = op_data.get("shapeId")
            if not isinstance(shape_id, str) or not shape_id:
                continue
            normalized = {"op": "delete_shape", "shapeId": shape_id}
            normalized_ops.append(normalized)
            _apply_virtual_operation(virtual_shapes, normalized)
            occupied = _occupied_bounds(virtual_shapes)

    for op_data in deferred_arrow_ops:
        if op_data.get("op") == "add_shape":
            normalized = _normalize_add_shape(op_data, virtual_shapes, occupied, defer_arrows=False)
        else:
            normalized = _normalize_update_shape(op_data, virtual_shapes)
        if normalized is None:
            continue
        normalized_ops.append(normalized)
        _apply_virtual_operation(virtual_shapes, normalized)
        occupied = _occupied_bounds(virtual_shapes)

    return normalized_ops


def sanitize_operations_for_apply(storage: dict, operations: object) -> list[dict]:
    """Repair malformed stored operations before applying them to storage."""
    raw_ops = [op for op in operations if isinstance(op, dict)] if isinstance(operations, list) else []
    virtual_shapes = deepcopy(_storage_shapes(storage))
    sanitized: list[dict] = []

    for op_data in raw_ops:
        op_name = op_data.get("op")
        if op_name == "add_shape":
            shape = op_data.get("shape")
            if not isinstance(shape, dict):
                continue
            normalized_shape = _normalize_shape_record(shape)
            if normalized_shape is None:
                continue
            if normalized_shape.get("type") == "arrow":
                normalized_shape = _normalize_arrow_shape(normalized_shape, virtual_shapes)
            sanitized_op = {"op": "add_shape", "shape": normalized_shape}
        elif op_name == "update_shape":
            sanitized_op = _normalize_update_shape(op_data, virtual_shapes)
            if sanitized_op is None:
                continue
        elif op_name == "delete_shape":
            shape_id = op_data.get("shapeId")
            if not isinstance(shape_id, str) or not shape_id:
                continue
            sanitized_op = {"op": "delete_shape", "shapeId": shape_id}
        else:
            continue

        sanitized.append(sanitized_op)
        _apply_virtual_operation(virtual_shapes, sanitized_op)

    return sanitized


def _normalize_add_shape(
    op_data: dict,
    virtual_shapes: dict[str, dict],
    occupied: list[Bounds],
    *,
    defer_arrows: bool,
) -> dict | None:
    shape_data = op_data.get("shape")
    if not isinstance(shape_data, dict):
        return None

    normalized_shape = _normalize_shape_record(shape_data)
    if normalized_shape is None:
        return None

    if normalized_shape["id"] in virtual_shapes:
        normalized_shape["id"] = f"shape:{uuid.uuid4().hex[:12]}"

    if normalized_shape.get("type") == "arrow":
        if defer_arrows:
            return {"op": "add_shape", "shape": normalized_shape}
        normalized_shape = _normalize_arrow_shape(normalized_shape, virtual_shapes)
    else:
        normalized_shape = _reflow_shape(normalized_shape, occupied)

    return {"op": "add_shape", "shape": normalized_shape}


def _normalize_update_shape(op_data: dict, virtual_shapes: dict[str, dict]) -> dict | None:
    shape_id = op_data.get("shapeId")
    updates = op_data.get("updates")
    if not isinstance(shape_id, str) or not shape_id or not isinstance(updates, dict):
        return None

    current_shape = virtual_shapes.get(shape_id)
    if not isinstance(current_shape, dict):
        return None

    normalized_updates = deepcopy(updates)
    if current_shape.get("type") == "arrow":
        normalized_updates = _normalize_arrow_updates(current_shape, normalized_updates, virtual_shapes)
    else:
        normalized_updates = _normalize_standard_updates(current_shape, normalized_updates)

    if not normalized_updates:
        return None

    return {
        "op": "update_shape",
        "shapeId": shape_id,
        "updates": normalized_updates,
    }


def _normalize_shape_record(shape_data: dict) -> dict | None:
    shape_type = shape_data.get("type")
    model = SHAPE_TYPE_MAP.get(shape_type)
    if model is None:
        return None

    try:
        normalized = model.model_validate(shape_data).model_dump(exclude_none=True)
    except ValidationError:
        return None

    normalized.setdefault("meta", {})
    return normalized


def _normalize_standard_updates(current_shape: dict, updates: dict) -> dict:
    merged = _deep_merge_dict(current_shape, updates)
    normalized = _normalize_shape_record(merged)
    if normalized is None:
        return updates

    result: dict = {}
    for key in ("x", "y", "rotation", "index", "parentId", "isLocked", "opacity"):
        if key in updates and normalized.get(key) != current_shape.get(key):
            result[key] = normalized.get(key)

    if "meta" in updates:
        result["meta"] = normalized.get("meta", {})
    if "props" in updates:
        result["props"] = normalized.get("props", {})

    return result or updates


def _normalize_arrow_updates(current_shape: dict, updates: dict, virtual_shapes: dict[str, dict]) -> dict:
    updates = _coerce_arrow_updates(updates)
    merged = _deep_merge_dict(current_shape, updates)
    normalized_arrow = _normalize_arrow_shape(merged, virtual_shapes)

    result: dict = {}
    for key in ("x", "y", "rotation", "index", "parentId", "isLocked", "opacity"):
        if normalized_arrow.get(key) != current_shape.get(key):
            result[key] = normalized_arrow.get(key)

    if normalized_arrow.get("props") != current_shape.get("props"):
        result["props"] = normalized_arrow.get("props", {})
    if normalized_arrow.get("meta") != current_shape.get("meta"):
        result["meta"] = normalized_arrow.get("meta", {})

    return result


def _coerce_arrow_updates(updates: dict) -> dict:
    coerced = deepcopy(updates)
    props_updates = coerced.get("props")
    if not isinstance(props_updates, dict):
        props_updates = {}
        coerced["props"] = props_updates

    for key in list(coerced.keys()):
        if key in ARROW_PROP_KEYS and key != "props":
            props_updates[key] = coerced.pop(key)

    return coerced


def _normalize_arrow_shape(shape: dict, virtual_shapes: dict[str, dict]) -> dict:
    try:
        arrow = ArrowShape.model_validate(shape).model_dump(exclude_none=True)
    except ValidationError:
        arrow = deepcopy(shape)

    connection = _extract_connection_spec(arrow, virtual_shapes)
    if connection is not None:
        return _apply_connection_to_arrow(arrow, connection, virtual_shapes)

    return _normalize_free_arrow_shape(arrow)


def _extract_connection_spec(arrow: dict, virtual_shapes: dict[str, dict]) -> ConnectionSpec | None:
    meta = arrow.get("meta")
    if isinstance(meta, dict):
        raw = meta.get(AGENT_CONNECTION_META_KEY)
        if isinstance(raw, dict):
            start_shape_id = raw.get("startShapeId")
            end_shape_id = raw.get("endShapeId")
            if (
                isinstance(start_shape_id, str)
                and isinstance(end_shape_id, str)
                and start_shape_id in virtual_shapes
                and end_shape_id in virtual_shapes
                and start_shape_id != end_shape_id
            ):
                return ConnectionSpec(
                    start_shape_id=start_shape_id,
                    end_shape_id=end_shape_id,
                    start_anchor=_normalize_anchor(raw.get("startAnchor")),
                    end_anchor=_normalize_anchor(raw.get("endAnchor")),
                )

    return _infer_connection_spec(arrow, virtual_shapes)


def _infer_connection_spec(arrow: dict, virtual_shapes: dict[str, dict]) -> ConnectionSpec | None:
    candidate_shapes = {
        shape_id: shape
        for shape_id, shape in virtual_shapes.items()
        if isinstance(shape, dict) and shape.get("type") != "arrow"
    }
    if len(candidate_shapes) < 2:
        return None

    props = arrow.get("props", {})
    start = _point(props.get("start"))
    end = _point(props.get("end"))
    arrow_x = _as_float(arrow.get("x"))
    arrow_y = _as_float(arrow.get("y"))

    absolute_points = (
        {"x": start["x"], "y": start["y"]},
        {"x": end["x"], "y": end["y"]},
    )
    local_points = (
        {"x": arrow_x + start["x"], "y": arrow_y + start["y"]},
        {"x": arrow_x + end["x"], "y": arrow_y + end["y"]},
    )
    start_origin_points = (
        {"x": arrow_x, "y": arrow_y},
        {"x": arrow_x + end["x"], "y": arrow_y + end["y"]},
    )

    best: tuple[float, ConnectionSpec] | None = None
    for start_point, end_point in (absolute_points, local_points, start_origin_points):
        spec = _match_shapes_for_endpoints(start_point, end_point, candidate_shapes)
        if spec is None:
            continue
        score = _connection_score(start_point, end_point, spec, candidate_shapes)
        if best is None or score < best[0]:
            best = (score, spec)

    return best[1] if best is not None else None


def _match_shapes_for_endpoints(
    start_point: dict[str, float],
    end_point: dict[str, float],
    candidate_shapes: dict[str, dict],
) -> ConnectionSpec | None:
    start_candidates = _sorted_nearest_shapes(start_point, candidate_shapes)
    end_candidates = _sorted_nearest_shapes(end_point, candidate_shapes)
    best: tuple[float, str, str] | None = None

    for start_shape_id, start_dist in start_candidates[:5]:
        for end_shape_id, end_dist in end_candidates[:5]:
            if start_shape_id == end_shape_id:
                continue
            max_dist = max(start_dist, end_dist)
            if max_dist > MAX_CONNECTION_ENDPOINT_DISTANCE:
                continue
            score = start_dist + end_dist
            if best is None or score < best[0]:
                best = (score, start_shape_id, end_shape_id)

    if best is None:
        return None

    return ConnectionSpec(
        start_shape_id=best[1],
        end_shape_id=best[2],
        start_anchor={"x": 0.5, "y": 0.5},
        end_anchor={"x": 0.5, "y": 0.5},
    )


def _connection_score(
    start_point: dict[str, float],
    end_point: dict[str, float],
    spec: ConnectionSpec,
    candidate_shapes: dict[str, dict],
) -> float:
    start_center = _shape_center(candidate_shapes[spec.start_shape_id])
    end_center = _shape_center(candidate_shapes[spec.end_shape_id])
    return _distance(start_point, start_center) + _distance(end_point, end_center)


def _sorted_nearest_shapes(point: dict[str, float], candidate_shapes: dict[str, dict]) -> list[tuple[str, float]]:
    scored = []
    for shape_id, shape in candidate_shapes.items():
        scored.append((shape_id, _distance(point, _shape_center(shape))))
    scored.sort(key=lambda item: item[1])
    return scored


def _apply_connection_to_arrow(arrow: dict, spec: ConnectionSpec, virtual_shapes: dict[str, dict]) -> dict:
    start_shape = virtual_shapes.get(spec.start_shape_id)
    end_shape = virtual_shapes.get(spec.end_shape_id)
    if not isinstance(start_shape, dict) or not isinstance(end_shape, dict):
        return _normalize_free_arrow_shape(arrow)

    start_point = _shape_anchor_point(start_shape, spec.start_anchor)
    end_point = _shape_anchor_point(end_shape, spec.end_anchor)
    if start_point == end_point:
        end_point = {"x": end_point["x"] + 1, "y": end_point["y"] + 1}

    origin = {
        "x": min(start_point["x"], end_point["x"]),
        "y": min(start_point["y"], end_point["y"]),
    }

    arrow["x"] = origin["x"]
    arrow["y"] = origin["y"]
    arrow.setdefault("props", {})
    arrow["props"]["start"] = {
        "x": start_point["x"] - origin["x"],
        "y": start_point["y"] - origin["y"],
    }
    arrow["props"]["end"] = {
        "x": end_point["x"] - origin["x"],
        "y": end_point["y"] - origin["y"],
    }
    arrow["meta"] = deepcopy(arrow.get("meta") or {})
    arrow["meta"][AGENT_CONNECTION_META_KEY] = {
        "startShapeId": spec.start_shape_id,
        "endShapeId": spec.end_shape_id,
        "startAnchor": spec.start_anchor,
        "endAnchor": spec.end_anchor,
    }
    return arrow


def _normalize_free_arrow_shape(arrow: dict) -> dict:
    props = arrow.setdefault("props", {})
    start = _point(props.get("start"))
    end = _point(props.get("end"))
    max_magnitude = max(abs(start["x"]), abs(start["y"]), abs(end["x"]), abs(end["y"]))

    if max_magnitude <= 100:
        props["start"] = start
        props["end"] = end
        arrow.setdefault("meta", {})
        return arrow

    origin = {
        "x": min(start["x"], end["x"]),
        "y": min(start["y"], end["y"]),
    }
    arrow["x"] = origin["x"]
    arrow["y"] = origin["y"]
    props["start"] = {
        "x": start["x"] - origin["x"],
        "y": start["y"] - origin["y"],
    }
    props["end"] = {
        "x": end["x"] - origin["x"],
        "y": end["y"] - origin["y"],
    }
    arrow.setdefault("meta", {})
    return arrow


def _reflow_shape(shape: dict, occupied: list[Bounds]) -> dict:
    bounds = _shape_bounds(shape)
    if bounds is None:
        return shape
    if not _collides(bounds, occupied):
        return shape

    preferred_x = max(0.0, bounds.x)
    preferred_y = max(0.0, bounds.y)
    step_x = max(bounds.w + MIN_SHAPE_GAP, 120.0)
    step_y = max(bounds.h + MIN_SHAPE_GAP, 120.0)

    candidates = [(preferred_x, preferred_y)]
    for radius in range(1, 8):
        candidates.extend([
            (max(0.0, preferred_x + radius * step_x), preferred_y),
            (max(0.0, preferred_x - radius * step_x), preferred_y),
            (preferred_x, max(0.0, preferred_y + radius * step_y)),
            (preferred_x, max(0.0, preferred_y - radius * step_y)),
        ])
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if max(abs(dx), abs(dy)) != radius:
                    continue
                if (dx == 0) ^ (dy == 0):
                    continue
                candidates.append((
                    max(0.0, preferred_x + dx * step_x),
                    max(0.0, preferred_y + dy * step_y),
                ))

    for x, y in candidates:
        candidate = Bounds(x=x, y=y, w=bounds.w, h=bounds.h)
        if not _collides(candidate, occupied):
            shape["x"] = x
            shape["y"] = y
            return shape

    max_right = max((item.x + item.w) for item in occupied) if occupied else 0.0
    shape["x"] = max_right + MIN_SHAPE_GAP
    shape["y"] = preferred_y
    return shape


def _collides(candidate: Bounds, occupied: list[Bounds]) -> bool:
    return any(_rects_overlap(candidate, other) for other in occupied)


def _rects_overlap(a: Bounds, b: Bounds) -> bool:
    return not (
        a.x + a.w + MIN_SHAPE_GAP <= b.x
        or b.x + b.w + MIN_SHAPE_GAP <= a.x
        or a.y + a.h + MIN_SHAPE_GAP <= b.y
        or b.y + b.h + MIN_SHAPE_GAP <= a.y
    )


def _occupied_bounds(shapes: dict[str, dict]) -> list[Bounds]:
    occupied = []
    for shape in shapes.values():
        bounds = _shape_bounds(shape)
        if bounds is not None:
            occupied.append(bounds)
    return occupied


def _shape_bounds(shape: dict) -> Bounds | None:
    if shape.get("parentId") != "page:page":
        return None

    shape_type = shape.get("type")
    if shape_type == "arrow":
        return None

    x = _as_float(shape.get("x"))
    y = _as_float(shape.get("y"))
    props = shape.get("props", {})

    if shape_type in {"geo", "frame"}:
        return Bounds(x=x, y=y, w=max(_as_float(props.get("w"), 1.0), 1.0), h=max(_as_float(props.get("h"), 1.0), 1.0))
    if shape_type == "note":
        return Bounds(x=x, y=y, w=DEFAULT_NOTE_WIDTH, h=DEFAULT_NOTE_HEIGHT)
    if shape_type == "text":
        return Bounds(x=x, y=y, w=max(_as_float(props.get("w"), DEFAULT_TEXT_WIDTH), 1.0), h=DEFAULT_TEXT_HEIGHT)
    if shape_type == "line":
        points = props.get("points", {})
        if isinstance(points, dict) and points:
            xs = [_as_float(point.get("x")) for point in points.values() if isinstance(point, dict)]
            ys = [_as_float(point.get("y")) for point in points.values() if isinstance(point, dict)]
            if xs and ys:
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                return Bounds(x=x + min_x, y=y + min_y, w=max(max_x - min_x, 1.0), h=max(max_y - min_y, 1.0))
    if shape_type == "draw":
        segments = props.get("segments", [])
        xs = []
        ys = []
        if isinstance(segments, list):
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                for point in segment.get("points", []):
                    if isinstance(point, dict):
                        xs.append(_as_float(point.get("x")))
                        ys.append(_as_float(point.get("y")))
        if xs and ys:
            min_x, max_x = min(xs), max(xs)
            min_y, max_y = min(ys), max(ys)
            return Bounds(x=x + min_x, y=y + min_y, w=max(max_x - min_x, 1.0), h=max(max_y - min_y, 1.0))

    return None


def _shape_center(shape: dict) -> dict[str, float]:
    bounds = _shape_bounds(shape)
    if bounds is not None:
        return {"x": bounds.x + bounds.w / 2, "y": bounds.y + bounds.h / 2}
    return {"x": _as_float(shape.get("x")), "y": _as_float(shape.get("y"))}


def _shape_anchor_point(shape: dict, anchor: dict[str, float]) -> dict[str, float]:
    bounds = _shape_bounds(shape)
    if bounds is None:
        center = _shape_center(shape)
        return center
    return {
        "x": bounds.x + bounds.w * anchor["x"],
        "y": bounds.y + bounds.h * anchor["y"],
    }


def _normalize_anchor(value: object) -> dict[str, float]:
    if isinstance(value, dict):
        return {
            "x": min(max(_as_float(value.get("x"), 0.5), 0.0), 1.0),
            "y": min(max(_as_float(value.get("y"), 0.5), 0.0), 1.0),
        }
    return {"x": 0.5, "y": 0.5}


def _storage_shapes(storage: dict) -> dict[str, dict]:
    shapes = storage.get("shapes")
    if not isinstance(shapes, dict):
        return {}
    return {shape_id: deepcopy(shape) for shape_id, shape in shapes.items() if isinstance(shape, dict)}


def _apply_virtual_operation(virtual_shapes: dict[str, dict], op_data: dict) -> None:
    op_name = op_data.get("op")
    if op_name == "add_shape":
        shape = op_data.get("shape")
        if isinstance(shape, dict) and isinstance(shape.get("id"), str):
            virtual_shapes[shape["id"]] = deepcopy(shape)
        return

    if op_name == "update_shape":
        shape_id = op_data.get("shapeId")
        updates = op_data.get("updates")
        current = virtual_shapes.get(shape_id)
        if isinstance(shape_id, str) and isinstance(current, dict) and isinstance(updates, dict):
            virtual_shapes[shape_id] = _deep_merge_dict(current, updates)
        return

    if op_name == "delete_shape":
        shape_id = op_data.get("shapeId")
        if isinstance(shape_id, str):
            virtual_shapes.pop(shape_id, None)


def _deep_merge_dict(base: dict, updates: dict) -> dict:
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = deepcopy(value)
    return merged


def _point(value: object) -> dict[str, float]:
    if isinstance(value, dict):
        return {
            "x": _as_float(value.get("x")),
            "y": _as_float(value.get("y")),
        }
    return {"x": 0.0, "y": 0.0}


def _distance(a: dict[str, float], b: dict[str, float]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _as_float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    return default


def _is_arrow_add(op_data: dict) -> bool:
    shape = op_data.get("shape")
    return isinstance(shape, dict) and shape.get("type") == "arrow"


def _targets_arrow(op_data: dict, virtual_shapes: dict[str, dict]) -> bool:
    shape_id = op_data.get("shapeId")
    shape = virtual_shapes.get(shape_id)
    return isinstance(shape_id, str) and isinstance(shape, dict) and shape.get("type") == "arrow"
