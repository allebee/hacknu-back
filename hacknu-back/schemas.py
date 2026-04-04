"""
Pydantic models for the AI Brainstorm Canvas agent service.

Defines:
- Request schema (PlanRequest) for the /api/agent/plan endpoint
- Response schema (PlanResponse)
- Typed action union (CanvasAction) with all supported canvas operations
- Supporting types for shapes, viewport, events
"""

from __future__ import annotations
from typing import Literal, Optional, Union, Any
from pydantic import BaseModel, Field
import uuid


# ── Supporting types ──────────────────────────────────────────────────────────

class ShapeSummary(BaseModel):
    """Compact representation of a shape on the canvas."""
    id: str = ""
    type: str = ""           # "geo", "arrow", "text", "note", "frame", "group", etc.
    text: str = ""
    x: float = 0
    y: float = 0
    w: float = 0
    h: float = 0
    color: str = ""
    parent_id: str = ""      # if inside a frame/group


class ViewportInfo(BaseModel):
    """Current viewport bounds and zoom level."""
    x: float = 0
    y: float = 0
    w: float = 1920
    h: float = 1080
    zoom: float = 1.0


class BoardEvent(BaseModel):
    """A recent edit event on the board."""
    type: Literal["created", "updated", "deleted"] = "created"
    shape_id: str = ""
    shape_type: str = ""
    text: str = ""
    timestamp: str = ""


# ── Canvas Actions (discriminated union on _type) ────────────────────────────

class CreateNote(BaseModel):
    _type: Literal["create_note"] = "create_note"
    action_type: Literal["create_note"] = "create_note"
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    x: float = 0
    y: float = 0
    w: float = 200
    h: float = 200
    text: str = ""
    color: str = "yellow"


class CreateArrow(BaseModel):
    _type: Literal["create_arrow"] = "create_arrow"
    action_type: Literal["create_arrow"] = "create_arrow"
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    from_id: str = ""
    to_id: str = ""
    label: str = ""


class CreateGroup(BaseModel):
    _type: Literal["create_group"] = "create_group"
    action_type: Literal["create_group"] = "create_group"
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    shape_ids: list[str] = []
    title: str = ""


class CreateFrame(BaseModel):
    _type: Literal["create_frame"] = "create_frame"
    action_type: Literal["create_frame"] = "create_frame"
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    x: float = 0
    y: float = 0
    w: float = 800
    h: float = 600
    title: str = ""


class CreateShape(BaseModel):
    """Create a geometric shape (rectangle, ellipse, diamond, etc.)."""
    _type: Literal["create_shape"] = "create_shape"
    action_type: Literal["create_shape"] = "create_shape"
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    x: float = 0
    y: float = 0
    w: float = 200
    h: float = 100
    geo: str = "rectangle"  # rectangle, ellipse, diamond, cloud, star, hexagon, etc.
    text: str = ""
    color: str = "blue"
    fill: str = "semi"  # none, semi, solid, pattern


class CreateText(BaseModel):
    """Create a plain text label (not inside a shape)."""
    _type: Literal["create_text"] = "create_text"
    action_type: Literal["create_text"] = "create_text"
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    x: float = 0
    y: float = 0
    text: str = ""
    size: str = "m"  # s, m, l, xl
    color: str = "black"


class MoveShapes(BaseModel):
    _type: Literal["move_shapes"] = "move_shapes"
    action_type: Literal["move_shapes"] = "move_shapes"
    shape_ids: list[str] = []
    dx: float = 0
    dy: float = 0


class UpdateText(BaseModel):
    _type: Literal["update_text"] = "update_text"
    action_type: Literal["update_text"] = "update_text"
    shape_id: str = ""
    text: str = ""


class DeleteShapes(BaseModel):
    _type: Literal["delete_shapes"] = "delete_shapes"
    action_type: Literal["delete_shapes"] = "delete_shapes"
    shape_ids: list[str] = []


class StyleShapes(BaseModel):
    _type: Literal["style_shapes"] = "style_shapes"
    action_type: Literal["style_shapes"] = "style_shapes"
    shape_ids: list[str] = []
    color: str = ""
    font: str = ""


class AlignShapes(BaseModel):
    _type: Literal["align_shapes"] = "align_shapes"
    action_type: Literal["align_shapes"] = "align_shapes"
    shape_ids: list[str] = []
    axis: Literal["horizontal", "vertical"] = "horizontal"


class DistributeShapes(BaseModel):
    _type: Literal["distribute_shapes"] = "distribute_shapes"
    action_type: Literal["distribute_shapes"] = "distribute_shapes"
    shape_ids: list[str] = []
    axis: Literal["horizontal", "vertical"] = "horizontal"


# Type alias for all canvas actions
CanvasAction = Union[
    CreateNote,
    CreateArrow,
    CreateGroup,
    CreateFrame,
    CreateShape,
    CreateText,
    MoveShapes,
    UpdateText,
    DeleteShapes,
    StyleShapes,
    AlignShapes,
    DistributeShapes,
]


# ── Request / Response ───────────────────────────────────────────────────────

class PlanRequest(BaseModel):
    """Input to the agent planner endpoint."""
    mode: Literal["ghostshape", "checkpoint", "create", "transform"]
    room_id: str = "default"
    user_intent: str = ""
    selection: list[ShapeSummary] = []
    viewport: ViewportInfo = ViewportInfo()
    visible_shapes: list[ShapeSummary] = []
    recent_events: list[BoardEvent] = []
    autonomy_level: Literal["suggest", "auto", "off"] = "suggest"
    focus_scope: str = ""


class PlanResponse(BaseModel):
    """Output from the agent planner endpoint."""
    actions: list[dict]  # list of action dicts with action_type discriminator
    reasoning: str = ""
    follow_up_delay_ms: int = 0
    mode: str = ""


# ── Helpers ──────────────────────────────────────────────────────────────────

ACTION_TYPES = {
    "create_note": CreateNote,
    "create_arrow": CreateArrow,
    "create_group": CreateGroup,
    "create_frame": CreateFrame,
    "create_shape": CreateShape,
    "create_text": CreateText,
    "move_shapes": MoveShapes,
    "update_text": UpdateText,
    "delete_shapes": DeleteShapes,
    "style_shapes": StyleShapes,
    "align_shapes": AlignShapes,
    "distribute_shapes": DistributeShapes,
}


def parse_action(data: dict) -> CanvasAction | None:
    """Parse a raw dict into a typed CanvasAction, or None if invalid."""
    action_type = data.get("action_type") or data.get("_type")
    if not action_type or action_type not in ACTION_TYPES:
        return None
    cls = ACTION_TYPES[action_type]
    try:
        return cls(**data)
    except Exception:
        return None
