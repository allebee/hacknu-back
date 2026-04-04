"""
Pydantic schemas for API request/response contracts.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, Field

from app.shapes import CanvasShape


# ── Pending Change (stored in Liveblocks + DB) ─────────────────────────

class ShapeOperation(BaseModel):
    """Single atomic operation within a pending change."""
    op: Literal["add_shape", "update_shape", "delete_shape"]
    shape: CanvasShape | None = None       # full shape for add_shape
    shapeId: str | None = None             # target for update/delete
    updates: dict | None = None            # partial update for update_shape


class PendingChange(BaseModel):
    id: str
    agentId: str
    status: Literal["pending"] = "pending"
    operations: list[ShapeOperation]
    reasoning: str = ""
    createdAt: str


# ── POST /complete ─────────────────────────────────────────────────────

class CompleteRequest(BaseModel):
    room_id: str


class CompleteResponse(BaseModel):
    change_id: str
    operations_count: int
    reasoning: str


# ── POST /complete/action ──────────────────────────────────────────────

class CompleteActionRequest(BaseModel):
    room_id: str
    change_id: str
    action: Literal["approve", "reject", "edit"]
    edit_prompt: str | None = None


class CompleteActionResponse(BaseModel):
    status: str = "ok"
    new_change_id: str | None = None
    reasoning: str | None = None
    operations_count: int = 0


# ── GET/POST /agents/{room_id} ─────────────────────────────────────────

class AgentInfo(BaseModel):
    id: str
    name: str
    type: Literal["autocomplete", "chatbot"]
    is_default: bool = False
    created_at: str


class ListAgentsResponse(BaseModel):
    agents: list[AgentInfo]


class CreateAgentRequest(BaseModel):
    name: str
    type: Literal["chatbot"] = "chatbot"


class CreateAgentResponse(BaseModel):
    agent: AgentInfo


# ── POST /agent/{agent_id}/run ─────────────────────────────────────────

class AgentRunRequest(BaseModel):
    room_id: str
    prompt: str
    mode: Literal["generate", "query"] = "generate"


class AgentRunResponse(BaseModel):
    change_id: str | None = None
    operations_count: int | None = None
    reasoning: str | None = None
    answer: str | None = None
    referenced_shapes: list[str] | None = None


# ── GET /agent/{agent_id}/messages ─────────────────────────────────────

class TextEntry(BaseModel):
    id: str
    type: Literal["text"] = "text"
    role: Literal["user", "assistant"]
    content: str
    created_at: str


class ChangeEntry(BaseModel):
    id: str
    type: Literal["change"] = "change"
    change_id: str
    change_status: Literal["pending", "approved", "rejected"]
    operations_summary: str
    created_at: str


ChatEntry = Union[TextEntry, ChangeEntry]


class MessagesResponse(BaseModel):
    messages: list[ChatEntry]


# ── Transcript endpoints ───────────────────────────────────────────────

class TranscriptChunk(BaseModel):
    speaker: str = "Unknown"
    text: str


class TranscriptPostRequest(BaseModel):
    chunks: list[TranscriptChunk]


class TranscriptPostResponse(BaseModel):
    room_id: str
    stored_count: int


class TranscriptGetResponse(BaseModel):
    room_id: str
    entry_count: int
    entries: list[dict]
    summary: str | None = None


class TranscriptDeleteResponse(BaseModel):
    room_id: str
    deleted_count: int
