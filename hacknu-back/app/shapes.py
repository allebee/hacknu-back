"""
Strict Pydantic schemas mirroring tldraw v3.10 shape types.

Used for:
  1. LLM structured output validation
  2. Liveblocks storage contract
  3. Frontend rendering correctness
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

DEFAULT_GEO_WIDTH = 260
DEFAULT_GEO_HEIGHT = 140


# ── Style Enums (from tldraw TLDefaultXStyle) ──────────────────────────

TLColor = Literal[
    "black", "grey", "light-violet", "violet", "blue", "light-blue",
    "yellow", "orange", "green", "light-green", "light-red", "red", "white",
]
TLFill = Literal["none", "semi", "solid", "pattern"]
TLDash = Literal["draw", "solid", "dashed", "dotted"]
TLSize = Literal["s", "m", "l", "xl"]
TLFont = Literal["draw", "sans", "serif", "mono"]
TLAlign = Literal["start", "middle", "end"]
TLVerticalAlign = Literal["start", "middle", "end"]
TLGeoType = Literal[
    "rectangle", "ellipse", "triangle", "diamond", "pentagon",
    "hexagon", "octagon", "star", "rhombus", "rhombus-2",
    "oval", "trapezoid", "arrow-right", "arrow-left",
    "arrow-up", "arrow-down", "x-box", "check-box",
    "cloud", "heart",
]
TLArrowheadStyle = Literal[
    "arrow", "triangle", "square", "dot", "pipe",
    "diamond", "inverted", "bar", "none",
]
TLArrowKind = Literal["arc", "elbow"]
TLSplineType = Literal["line", "cubic"]


# ── Shared sub-models ──────────────────────────────────────────────────

class VecModel(BaseModel):
    x: float = 0
    y: float = 0


class RichText(BaseModel):
    """Prosemirror-compatible rich text node."""
    type: str = "doc"
    content: list[dict] = []


# ── Base Shape ─────────────────────────────────────────────────────────

class TLBaseShape(BaseModel):
    """Common fields for ALL tldraw shapes."""
    id: str
    type: str
    x: float = 0
    y: float = 0
    rotation: float = 0
    index: str = "a1"
    parentId: str = "page:page"
    isLocked: bool = False
    opacity: float = 1.0
    meta: dict = {}


# ── Geo Shape ──────────────────────────────────────────────────────────

class GeoShapeProps(BaseModel):
    geo: TLGeoType = "rectangle"
    w: float = DEFAULT_GEO_WIDTH
    h: float = DEFAULT_GEO_HEIGHT
    color: TLColor = "black"
    fill: TLFill = "solid"
    dash: TLDash = "solid"
    size: TLSize = "m"
    font: TLFont = "draw"
    align: TLAlign = "middle"
    verticalAlign: TLVerticalAlign = "middle"
    richText: RichText = RichText()
    labelColor: TLColor = "black"
    url: str = ""
    growY: float = 0
    scale: float = 1


class GeoShape(TLBaseShape):
    type: Literal["geo"] = "geo"
    props: GeoShapeProps = GeoShapeProps()


# ── Arrow Shape ────────────────────────────────────────────────────────

class ArrowShapeProps(BaseModel):
    kind: TLArrowKind = "arc"
    start: VecModel = VecModel()
    end: VecModel = VecModel(x=100, y=100)
    bend: float = 0
    color: TLColor = "black"
    fill: TLFill = "none"
    dash: TLDash = "solid"
    size: TLSize = "m"
    font: TLFont = "draw"
    arrowheadStart: TLArrowheadStyle = "none"
    arrowheadEnd: TLArrowheadStyle = "arrow"
    labelColor: TLColor = "black"
    labelPosition: float = 0.5
    richText: RichText = RichText()
    scale: float = 1
    elbowMidPoint: float = 0.5


class ArrowShape(TLBaseShape):
    type: Literal["arrow"] = "arrow"
    props: ArrowShapeProps = ArrowShapeProps()


# ── Note (Sticky Note) ─────────────────────────────────────────────────

class NoteShapeProps(BaseModel):
    color: TLColor = "yellow"
    labelColor: TLColor = "black"
    size: TLSize = "m"
    font: TLFont = "draw"
    fontSizeAdjustment: int = 0
    align: TLAlign = "middle"
    verticalAlign: TLVerticalAlign = "middle"
    growY: float = 0
    url: str = ""
    richText: RichText = RichText()
    scale: float = 1


class NoteShape(TLBaseShape):
    type: Literal["note"] = "note"
    props: NoteShapeProps = NoteShapeProps()


# ── Text ───────────────────────────────────────────────────────────────

class TextShapeProps(BaseModel):
    color: TLColor = "black"
    size: TLSize = "m"
    font: TLFont = "draw"
    textAlign: TLAlign = "start"
    w: float = 200
    richText: RichText = RichText()
    scale: float = 1
    autoSize: bool = True


class TextShape(TLBaseShape):
    type: Literal["text"] = "text"
    props: TextShapeProps = TextShapeProps()


# ── Frame ──────────────────────────────────────────────────────────────

class FrameShapeProps(BaseModel):
    w: float = 400
    h: float = 300
    name: str = ""
    color: TLColor = "black"


class FrameShape(TLBaseShape):
    type: Literal["frame"] = "frame"
    props: FrameShapeProps = FrameShapeProps()


# ── Line ───────────────────────────────────────────────────────────────

class LinePoint(BaseModel):
    id: str
    index: str
    x: float
    y: float


class LineShapeProps(BaseModel):
    color: TLColor = "black"
    dash: TLDash = "solid"
    size: TLSize = "m"
    spline: TLSplineType = "line"
    points: dict[str, LinePoint] = {}
    scale: float = 1


class LineShape(TLBaseShape):
    type: Literal["line"] = "line"
    props: LineShapeProps = LineShapeProps()


# ── Draw (Freehand) ───────────────────────────────────────────────────

class DrawPoint(BaseModel):
    x: float
    y: float
    z: float = 0.5


class DrawSegment(BaseModel):
    type: Literal["free", "straight"] = "free"
    points: list[DrawPoint] = []


class DrawShapeProps(BaseModel):
    color: TLColor = "black"
    fill: TLFill = "none"
    dash: TLDash = "solid"
    size: TLSize = "m"
    segments: list[DrawSegment] = []
    isComplete: bool = True
    isClosed: bool = False
    isPen: bool = False
    scale: float = 1


class DrawShape(TLBaseShape):
    type: Literal["draw"] = "draw"
    props: DrawShapeProps = DrawShapeProps()


# ── Group ──────────────────────────────────────────────────────────────

class GroupShape(TLBaseShape):
    type: Literal["group"] = "group"
    props: dict = {}


# ── Discriminated Union ────────────────────────────────────────────────

CanvasShape = Annotated[
    Union[
        GeoShape, ArrowShape, NoteShape, TextShape,
        FrameShape, LineShape, DrawShape, GroupShape,
    ],
    Field(discriminator="type"),
]

# Map of type string → Pydantic class for dynamic parsing
SHAPE_TYPE_MAP: dict[str, type[TLBaseShape]] = {
    "geo": GeoShape,
    "arrow": ArrowShape,
    "note": NoteShape,
    "text": TextShape,
    "frame": FrameShape,
    "line": LineShape,
    "draw": DrawShape,
    "group": GroupShape,
}
