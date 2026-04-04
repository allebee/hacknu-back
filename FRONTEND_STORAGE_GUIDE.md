# Frontend Storage Guide

> This document defines the **Liveblocks Storage schema** and the **exact shape contracts** the frontend must follow when reading/writing canvas data. All shapes written to storage must match these types exactly — the backend validates against the same schemas.

---

## Storage Root Structure

```typescript
// liveblocks.config.ts
type Storage = {
  shapes: LiveMap<string, CanvasShape>;           // committed shapes on canvas
  pendingChanges: LiveMap<string, PendingChange>;  // agent suggestions (ghost overlays)
  agents: LiveMap<string, AgentInfo>;              // registered agents
  meta: LiveObject<RoomMeta>;                      // room metadata
};
```

---

## Shape Types

Every shape shares a common base, discriminated by `type`. The `props` object is type-specific.

### Base Shape (all types)

```typescript
interface TLBaseShape {
  id: string;           // "shape:xxxx" — unique ID
  type: string;         // discriminator: "geo" | "arrow" | "note" | "text" | "frame" | "line" | "draw" | "group"
  x: number;            // canvas X position
  y: number;            // canvas Y position
  rotation: number;     // radians, default 0
  index: string;        // fractional index for z-ordering, e.g. "a1"
  parentId: string;     // "page:page" | frame/group ID
  isLocked: boolean;    // default false
  opacity: number;      // 0-1, default 1
  meta: Record<string, any>;  // custom metadata
}
```

### Style Enums (shared across types)

```typescript
type TLColor = "black" | "grey" | "light-violet" | "violet" | "blue" | "light-blue"
             | "yellow" | "orange" | "green" | "light-green" | "light-red" | "red" | "white";

type TLFill = "none" | "semi" | "solid" | "pattern";
type TLDash = "draw" | "solid" | "dashed" | "dotted";
type TLSize = "s" | "m" | "l" | "xl";
type TLFont = "draw" | "sans" | "serif" | "mono";
type TLAlign = "start" | "middle" | "end";
type TLVerticalAlign = "start" | "middle" | "end";
```

### Rich Text Format (Prosemirror JSON)

All text content uses Prosemirror format:

```typescript
// Empty text
{ type: "doc", content: [] }

// "Hello World"
{
  type: "doc",
  content: [{
    type: "paragraph",
    content: [{ type: "text", text: "Hello World" }]
  }]
}
```

---

## Per-Type Props

### `geo` — Rectangle, Ellipse, Diamond, Cloud, etc.

```typescript
type TLGeoType = "rectangle" | "ellipse" | "triangle" | "diamond" | "pentagon"
               | "hexagon" | "octagon" | "star" | "rhombus" | "rhombus-2"
               | "oval" | "trapezoid" | "arrow-right" | "arrow-left"
               | "arrow-up" | "arrow-down" | "x-box" | "check-box"
               | "cloud" | "heart";

interface GeoShapeProps {
  geo: TLGeoType;              // default: "rectangle"
  w: number;                   // width, default: 200
  h: number;                   // height, default: 100
  color: TLColor;              // default: "black"
  fill: TLFill;                // default: "solid"
  dash: TLDash;                // default: "solid"
  size: TLSize;                // default: "m"
  font: TLFont;                // default: "draw"
  align: TLAlign;              // default: "middle"
  verticalAlign: TLVerticalAlign; // default: "middle"
  richText: RichText;          // label text
  labelColor: TLColor;         // default: "black"
  url: string;                 // default: ""
  growY: number;               // auto-grow height, default: 0
  scale: number;               // default: 1
}
```

**Example:**
```json
{
  "id": "shape:rect1", "type": "geo",
  "x": 100, "y": 200, "rotation": 0, "index": "a1",
  "parentId": "page:page", "isLocked": false, "opacity": 1,
  "props": {
    "geo": "rectangle", "w": 200, "h": 100,
    "color": "blue", "fill": "solid", "dash": "solid",
    "size": "m", "font": "draw", "align": "middle", "verticalAlign": "middle",
    "richText": {"type":"doc","content":[{"type":"paragraph","content":[{"type":"text","text":"Hello"}]}]},
    "labelColor": "black", "url": "", "growY": 0, "scale": 1
  },
  "meta": {}
}
```

### `arrow` — Connections between shapes

```typescript
type TLArrowheadStyle = "arrow" | "triangle" | "square" | "dot" | "pipe"
                      | "diamond" | "inverted" | "bar" | "none";
type TLArrowKind = "arc" | "elbow";

interface ArrowShapeProps {
  kind: TLArrowKind;                 // default: "arc"
  start: { x: number; y: number };   // start point
  end: { x: number; y: number };     // end point
  bend: number;                      // curve amount, default: 0
  color: TLColor;                    // default: "black"
  fill: TLFill;                      // default: "none"
  dash: TLDash;                      // default: "solid"
  size: TLSize;                      // default: "m"
  font: TLFont;                      // default: "draw"
  arrowheadStart: TLArrowheadStyle;  // default: "none"
  arrowheadEnd: TLArrowheadStyle;    // default: "arrow"
  labelColor: TLColor;               // default: "black"
  labelPosition: number;             // 0-1 along arrow, default: 0.5
  richText: RichText;                // label text
  scale: number;                     // default: 1
  elbowMidPoint: number;             // for elbow arrows, default: 0.5
}
```

> **Note on bindings:** Arrow-to-shape connections (bindings) are handled by the frontend via `editor.createBindings()`. The storage only stores `start`/`end` coordinates — the frontend snaps arrows to shapes.
>
> Agent-generated arrows may also include:
> ```typescript
> meta: {
>   agentConnection?: {
>     startShapeId: string
>     endShapeId: string
>     startAnchor?: { x: number; y: number } // normalized 0..1
>     endAnchor?: { x: number; y: number }   // normalized 0..1
>   }
> }
> ```
> The backend uses this metadata to compute a visually correct arrow. The frontend should use it to create actual tldraw bindings when the change is approved.

### `note` — Sticky Note

```typescript
interface NoteShapeProps {
  color: TLColor;              // default: "yellow"
  labelColor: TLColor;         // default: "black"
  size: TLSize;                // default: "m"
  font: TLFont;                // default: "draw"
  fontSizeAdjustment: number;  // default: 0
  align: TLAlign;              // default: "middle"
  verticalAlign: TLVerticalAlign; // default: "middle"
  growY: number;               // default: 0
  url: string;                 // default: ""
  richText: RichText;          // note content
  scale: number;               // default: 1
}
```

### `text` — Free Text

```typescript
interface TextShapeProps {
  color: TLColor;        // default: "black"
  size: TLSize;          // default: "m"
  font: TLFont;          // default: "draw"
  textAlign: TLAlign;    // default: "start"
  w: number;             // width, default: 200
  richText: RichText;    // text content
  scale: number;         // default: 1
  autoSize: boolean;     // default: true
}
```

### `frame` — Container

```typescript
interface FrameShapeProps {
  w: number;          // width, default: 400
  h: number;          // height, default: 300
  name: string;       // frame label, default: ""
  color: TLColor;     // default: "black"
}
```

### `line` — Multi-point Line/Spline

```typescript
type TLSplineType = "line" | "cubic";

interface LineShapeProps {
  color: TLColor;
  dash: TLDash;
  size: TLSize;
  spline: TLSplineType;                        // default: "line"
  points: Record<string, {                     // keyed by point ID
    id: string; index: string; x: number; y: number;
  }>;
  scale: number;
}
```

### `draw` — Freehand Drawing

```typescript
interface DrawShapeProps {
  color: TLColor;
  fill: TLFill;
  dash: TLDash;
  size: TLSize;
  segments: Array<{
    type: "free" | "straight";
    points: Array<{ x: number; y: number; z: number }>; // z = pressure
  }>;
  isComplete: boolean;
  isClosed: boolean;
  isPen: boolean;
  scale: number;
}
```

### `group` — Group Container

```typescript
// Groups have no props. Children reference the group via their parentId.
interface GroupShapeProps {}
```

---

## Pending Changes (Agent Suggestions)

The backend writes agent-generated suggestions to `pendingChanges`. The frontend renders them as **ghost overlays** with approve/reject buttons.

```typescript
interface PendingChange {
  id: string;            // UUID string, same as agent_changes.id
  agentId: string;       // which agent created this
  status: "pending";
  operations: Array<{
    op: "add_shape" | "update_shape" | "delete_shape";
    shape?: CanvasShape;   // full shape for add_shape
    shapeId?: string;      // target for update/delete
    updates?: Partial<any>; // partial update for update_shape
  }>;
  reasoning: string;
  createdAt: string;       // ISO 8601
}
```

### Frontend should:

1. **Subscribe** to `pendingChanges` via `useStorage`
2. **Filter by `agentId`** to show per-agent approve/reject buttons
3. **Render `add_shape` operations** as semi-transparent ghost shapes
4. On **Approve** → call `POST /complete/action` with `action: "approve"`
5. On **Reject** → call `POST /complete/action` with `action: "reject"`
6. On **Edit** → open chatbot sidebar, call `POST /complete/action` with `action: "edit"` and `edit_prompt`

### Multiple agents at once:

```
pendingChanges: {
  "0d6d8557-4778-40fd-bfd0-8cb89b1685d9": { agentId: "agent_0_room1", ... },  // default room agent
  "de37619c-d4b8-45fd-97e9-4de3dbf8b7fc": { agentId: "agent_abc_room1", ... }, // chatbot agent
}

// Each agent's changes have independent approve/reject
// Approving one UUID-keyed change does NOT affect another
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/complete` | Trigger autocomplete (send `{ room_id }`) |
| `POST` | `/complete/action` | Approve/reject/edit a pending change |
| `GET` | `/agents/{room_id}` | List agents in room and ensure `agent_0` exists |
| `POST` | `/agents/{room_id}` | Create new chatbot agent |
| `POST` | `/agent/{agent_id}/run` | Send chat message to agent |
| `GET` | `/agent/{agent_id}/messages` | Get chat history timeline |

Full Swagger docs: `http://localhost:8000/docs`
