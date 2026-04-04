"""
API route handlers.
"""

from __future__ import annotations

from copy import deepcopy
import uuid
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Agent, AgentChange, ChatMessage
from app.schemas import (
    AgentInfo,
    AgentRunRequest,
    AgentRunResponse,
    CompleteActionRequest,
    CompleteActionResponse,
    CompleteRequest,
    CompleteResponse,
    CreateAgentRequest,
    CreateAgentResponse,
    ListAgentsResponse,
    MessagesResponse,
    TextEntry,
    ChangeEntry,
    TranscriptPostRequest,
    TranscriptPostResponse,
    TranscriptGetResponse,
    TranscriptDeleteResponse,
)
from app.liveblocks import liveblocks
from app.operations import sanitize_operations_for_apply
from app.planner import generate_operations, generate_query_answer
from app.transcript import store_chunks, get_meeting_context, get_transcript_entries, clear_transcript

logger = logging.getLogger(__name__)

DbDep = Annotated[AsyncSession, Depends(get_db)]

# ── Routers ────────────────────────────────────────────────────────────

complete_router = APIRouter(tags=["complete"])
agents_router = APIRouter(tags=["agents"])
agent_router = APIRouter(tags=["agent"])
transcript_router = APIRouter(tags=["transcript"])

DEFAULT_AGENT_NAME = "Agent 0"


def _default_agent_id(room_id: str) -> str:
    return f"agent_0_{room_id}"


def _new_change_identity() -> tuple[uuid.UUID, str]:
    change_uuid = uuid.uuid4()
    return change_uuid, str(change_uuid)


def _get_pending_change_entry(storage: dict, change_id: str) -> dict | None:
    pending_changes = storage.get("pendingChanges")
    if isinstance(pending_changes, dict):
        entry = pending_changes.get(change_id)
        if isinstance(entry, dict):
            return entry
    return None


def _normalize_operations(operations: object) -> list[dict]:
    if isinstance(operations, list):
        return [op for op in operations if isinstance(op, dict)]
    return []


def _match_pending_db_change(
    pending_rows: list[AgentChange],
    public_change_id: str,
    pending_entry: dict | None,
) -> AgentChange | None:
    try:
        requested_uuid = uuid.UUID(public_change_id)
    except ValueError:
        requested_uuid = None

    for row in pending_rows:
        if requested_uuid and row.id == requested_uuid:
            return row
        if public_change_id == str(row.id):
            return row

    if not pending_entry:
        return None

    target_agent_id = pending_entry.get("agentId")
    target_reasoning = pending_entry.get("reasoning")
    target_operations = _normalize_operations(pending_entry.get("operations"))

    for row in pending_rows:
        if target_agent_id and row.agent_id != target_agent_id:
            continue
        if target_reasoning is not None and row.reasoning != target_reasoning:
            continue
        if target_operations and _normalize_operations(row.operations) == target_operations:
            return row

    for row in pending_rows:
        if target_agent_id and row.agent_id != target_agent_id:
            continue
        if target_reasoning and row.reasoning == target_reasoning:
            return row

    return None


def _deep_merge_dict(base: dict, updates: dict) -> dict:
    merged = deepcopy(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def _build_edit_user_prompt(edit_prompt: str, previous_reasoning: str = "") -> str:
    feedback = edit_prompt.strip()
    if not previous_reasoning:
        return feedback

    return "\n".join(
        [
            "Revise the previous pending canvas suggestion.",
            f"Previous suggestion: {previous_reasoning}",
            f"User edit request: {feedback}",
            "Prefer updating the existing suggested relationship instead of asking for confirmation when a sensible interpretation exists.",
            "If multiple shapes match by label, choose the most sensible one based on the current canvas.",
        ]
    )


async def ensure_default_agent(room_id: str, db: AsyncSession) -> Agent:
    """Ensure the room has the shared default chatbot agent."""
    agent_id = _default_agent_id(room_id)
    agent = await db.get(Agent, agent_id)

    if not agent:
        db.add(
            Agent(
                id=agent_id,
                room_id=room_id,
                name=DEFAULT_AGENT_NAME,
                type="chatbot",
                is_default=True,
            )
        )
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
        agent = await db.get(Agent, agent_id)

    if not agent:
        raise HTTPException(status_code=500, detail="Failed to initialize default agent")

    changed = False
    if agent.name != DEFAULT_AGENT_NAME:
        agent.name = DEFAULT_AGENT_NAME
        changed = True
    if agent.type != "chatbot":
        agent.type = "chatbot"
        changed = True
    if not agent.is_default:
        agent.is_default = True
        changed = True

    if changed:
        await db.commit()

    await db.refresh(agent)
    return agent


async def sync_agent_to_storage(room_id: str, agent: Agent) -> None:
    """Best-effort mirror of agent registry into Liveblocks storage."""
    try:
        await liveblocks.patch_storage(
            room_id,
            [
                {
                    "op": "add",
                    "path": f"/agents/{agent.id}",
                    "value": {
                        "id": agent.id,
                        "name": agent.name,
                        "type": agent.type,
                        "isDefault": agent.is_default,
                        "createdAt": agent.created_at.isoformat(),
                    },
                }
            ],
        )
    except Exception as e:
        logger.warning(f"Failed to write agent to storage: {e}")


# ───────────────────────────────────────────────────────────────────────
# POST /complete
# ───────────────────────────────────────────────────────────────────────

@complete_router.post("/complete", response_model=CompleteResponse)
async def autocomplete(req: CompleteRequest, db: DbDep):
    """Autocomplete endpoint — triggered after idle. Uses the room's default agent."""
    agent = await ensure_default_agent(req.room_id, db)
    agent_id = agent.id

    # Set presence
    try:
        await liveblocks.set_presence(req.room_id, agent_id, "thinking", ttl=30)
    except Exception as e:
        logger.warning(f"Failed to set presence: {e}")

    # Get current storage
    try:
        storage = await liveblocks.get_storage(req.room_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read storage: {e}")

    # Get rejected history
    result = await db.execute(
        select(AgentChange)
        .where(AgentChange.agent_id == agent_id, AgentChange.status == "rejected")
        .order_by(AgentChange.created_at.desc())
        .limit(10)
    )
    rejected = [{"reasoning": r.reasoning, "operations": r.operations} for r in result.scalars()]

    # Generate operations
    operations, reasoning = await generate_operations(
        storage,
        rejected_ops=rejected,
        meeting_context=await get_meeting_context(db, req.room_id),
        request_mode="autocomplete",
    )

    if not operations:
        await liveblocks.set_presence(req.room_id, agent_id, "idle", ttl=5)
        return CompleteResponse(change_id="", operations_count=0, reasoning=reasoning or "No suggestions")

    # Save to DB
    change_uuid, change_id = _new_change_identity()
    change = AgentChange(
        id=change_uuid,
        room_id=req.room_id,
        agent_id=agent_id,
        status="pending",
        operations=[op if isinstance(op, dict) else op for op in operations],
        reasoning=reasoning,
    )
    db.add(change)
    await db.commit()

    # Write to Liveblocks pendingChanges
    now = datetime.now(timezone.utc).isoformat()
    try:
        await liveblocks.patch_storage(req.room_id, [
            {
                "op": "add",
                "path": f"/pendingChanges/{change_id}",
                "value": {
                    "id": change_id,
                    "agentId": agent_id,
                    "status": "pending",
                    "operations": operations,
                    "reasoning": reasoning,
                    "createdAt": now,
                },
            }
        ])
    except Exception as e:
        logger.error(f"Failed to write pending change: {e}")
        raise HTTPException(status_code=502, detail=f"Storage write failed: {e}")

    await liveblocks.set_presence(req.room_id, agent_id, "suggested", ttl=10)

    return CompleteResponse(
        change_id=change_id,
        operations_count=len(operations),
        reasoning=reasoning,
    )


# ───────────────────────────────────────────────────────────────────────
# POST /complete/action
# ───────────────────────────────────────────────────────────────────────

@complete_router.post("/complete/action", response_model=CompleteActionResponse)
async def complete_action(req: CompleteActionRequest, db: DbDep):
    """Approve, reject, or edit a pending change."""

    try:
        storage = await liveblocks.get_storage(req.room_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read storage: {e}")

    pending_entry = _get_pending_change_entry(storage, req.change_id)

    # Find the change in DB
    result = await db.execute(
        select(AgentChange).where(
            AgentChange.room_id == req.room_id,
            AgentChange.status == "pending",
        ).order_by(AgentChange.created_at.desc())
    )
    pending_rows = list(result.scalars())
    change = _match_pending_db_change(pending_rows, req.change_id, pending_entry)

    if not pending_entry and not change:
        raise HTTPException(status_code=404, detail=f"Pending change not found: {req.change_id}")

    source_operations = _normalize_operations(
        pending_entry.get("operations") if pending_entry else (change.operations if change else [])
    )
    source_reasoning = (
        (pending_entry.get("reasoning") or "") if pending_entry
        else (change.reasoning or "")
    )
    source_agent_id = (
        str(pending_entry.get("agentId")) if pending_entry and pending_entry.get("agentId")
        else (change.agent_id if change else _default_agent_id(req.room_id))
    )

    if req.action == "approve":
        # Move shapes from operations to /shapes
        if pending_entry or change:
            shapes = storage.get("shapes", {})
            source_operations = sanitize_operations_for_apply(storage, source_operations)
            ops_patch = []
            for op_data in source_operations:
                op_name = op_data.get("op")
                if op_name == "add_shape":
                    shape = op_data.get("shape", {})
                    shape_id = shape.get("id", f"shape:{uuid.uuid4().hex[:8]}")
                    ops_patch.append({
                        "op": "add",
                        "path": f"/shapes/{shape_id}",
                        "value": shape,
                    })
                elif op_name == "delete_shape":
                    sid = op_data.get("shapeId", "")
                    if sid:
                        ops_patch.append({"op": "remove", "path": f"/shapes/{sid}"})
                elif op_name == "update_shape":
                    sid = op_data.get("shapeId", "")
                    updates = op_data.get("updates")
                    current_shape = shapes.get(sid) if isinstance(shapes, dict) else None
                    if not sid or not isinstance(updates, dict) or not isinstance(current_shape, dict):
                        raise HTTPException(
                            status_code=409,
                            detail=f"Cannot apply update_shape for {sid or 'unknown shape'}",
                        )
                    ops_patch.append({
                        "op": "add",
                        "path": f"/shapes/{sid}",
                        "value": _deep_merge_dict(current_shape, updates),
                    })

            if pending_entry:
                # Remove from pendingChanges
                ops_patch.append({"op": "remove", "path": f"/pendingChanges/{req.change_id}"})

            if not ops_patch:
                raise HTTPException(status_code=409, detail="Pending change has no applicable operations")

            try:
                await liveblocks.patch_storage(req.room_id, ops_patch)
            except Exception as e:
                logger.error(f"Approve patch failed: {e}")
                raise HTTPException(status_code=502, detail=str(e))

            if change:
                change.status = "approved"
                change.resolved_at = datetime.now(timezone.utc)
                await db.commit()

        return CompleteActionResponse(status="approved")

    elif req.action == "reject":
        # Remove from Liveblocks
        if pending_entry:
            try:
                await liveblocks.patch_storage(req.room_id, [
                    {"op": "remove", "path": f"/pendingChanges/{req.change_id}"}
                ])
            except Exception as e:
                logger.warning(f"Reject patch failed: {e}")

        if change:
            change.status = "rejected"
            change.user_feedback = req.edit_prompt
            change.resolved_at = datetime.now(timezone.utc)
            await db.commit()

        return CompleteActionResponse(status="rejected")

    elif req.action == "edit":
        if not req.edit_prompt:
            raise HTTPException(status_code=400, detail="edit_prompt required for edit action")

        # Remove old pending
        if pending_entry:
            try:
                await liveblocks.patch_storage(req.room_id, [
                    {"op": "remove", "path": f"/pendingChanges/{req.change_id}"}
                ])
            except Exception as e:
                logger.warning(f"Edit patch remove failed: {e}")

            pending_changes = storage.get("pendingChanges")
            if isinstance(pending_changes, dict):
                pending_changes.pop(req.change_id, None)

        if change:
            change.status = "rejected"
            change.user_feedback = req.edit_prompt
            change.resolved_at = datetime.now(timezone.utc)
            await db.commit()

        # Re-run with edit context
        edit_user_prompt = _build_edit_user_prompt(req.edit_prompt, source_reasoning)
        operations, reasoning = await generate_operations(
            storage,
            user_prompt=edit_user_prompt,
            rejected_ops=[{"reasoning": source_reasoning, "operations": source_operations}] if source_operations or source_reasoning else None,
            meeting_context=await get_meeting_context(db, req.room_id),
            request_mode="chat_generate",
            include_full_storage=True,
        )

        if not operations:
            return CompleteActionResponse(
                status="no_change",
                reasoning=reasoning or "No canvas update was generated.",
                operations_count=0,
            )

        new_change_uuid, new_change_id = _new_change_identity()
        new_change = AgentChange(
            id=new_change_uuid,
            room_id=req.room_id,
            agent_id=source_agent_id,
            status="pending",
            operations=operations,
            reasoning=reasoning,
        )
        db.add(new_change)
        await db.commit()

        now = datetime.now(timezone.utc).isoformat()
        await liveblocks.patch_storage(req.room_id, [
            {
                "op": "add",
                "path": f"/pendingChanges/{new_change_id}",
                "value": {
                    "id": new_change_id,
                    "agentId": source_agent_id,
                    "status": "pending",
                    "operations": operations,
                    "reasoning": reasoning,
                    "createdAt": now,
                },
            }
        ])

        return CompleteActionResponse(
            status="edited",
            new_change_id=new_change_id,
            reasoning=reasoning,
            operations_count=len(operations),
        )

    raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")


# ───────────────────────────────────────────────────────────────────────
# GET /agents/{room_id}
# ───────────────────────────────────────────────────────────────────────

@agents_router.get("/agents/{room_id}", response_model=ListAgentsResponse)
async def list_agents(room_id: str, db: DbDep):
    default_agent = await ensure_default_agent(room_id, db)
    await sync_agent_to_storage(room_id, default_agent)

    result = await db.execute(
        select(Agent)
        .where(Agent.room_id == room_id)
        .order_by(Agent.is_default.desc(), Agent.created_at)
    )
    agents = result.scalars().all()
    return ListAgentsResponse(
        agents=[
            AgentInfo(
                id=a.id,
                name=a.name,
                type=a.type,
                is_default=a.is_default,
                created_at=a.created_at.isoformat(),
            )
            for a in agents
        ]
    )


# ───────────────────────────────────────────────────────────────────────
# POST /agents/{room_id}
# ───────────────────────────────────────────────────────────────────────

@agents_router.post("/agents/{room_id}", response_model=CreateAgentResponse)
async def create_agent(room_id: str, req: CreateAgentRequest, db: DbDep):
    agent_id = f"agent_{uuid.uuid4().hex[:8]}_{room_id}"
    now = datetime.now(timezone.utc)

    agent = Agent(
        id=agent_id,
        room_id=room_id,
        name=req.name,
        type=req.type,
        is_default=False,
        created_at=now,
    )
    db.add(agent)
    await db.commit()

    # Write to Liveblocks agents LiveMap
    await sync_agent_to_storage(room_id, agent)

    return CreateAgentResponse(
        agent=AgentInfo(
            id=agent_id,
            name=req.name,
            type=req.type,
            is_default=False,
            created_at=now.isoformat(),
        )
    )


# ───────────────────────────────────────────────────────────────────────
# POST /agent/{agent_id}/run
# ───────────────────────────────────────────────────────────────────────

@agent_router.post("/agent/{agent_id}/run", response_model=AgentRunResponse)
async def run_agent(agent_id: str, req: AgentRunRequest, db: DbDep):
    # Verify agent exists
    agent = await db.get(Agent, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    # Get storage
    try:
        storage = await liveblocks.get_storage(req.room_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read storage: {e}")

    # Get chat history
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.agent_id == agent_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(100)
    )
    chat_history = []
    for m in reversed(result.scalars().all()):
        if m.type == "change":
            chat_history.append(
                {
                    "type": "change",
                    "role": "assistant",
                    "change_status": m.change_status,
                    "operations_summary": m.operations_summary,
                }
            )
        else:
            chat_history.append(
                {
                    "type": "text",
                    "role": m.role,
                    "content": m.content,
                }
            )

    # Save user message
    user_msg = ChatMessage(
        agent_id=agent_id,
        type="text",
        role="user",
        content=req.prompt,
    )
    db.add(user_msg)

    if req.mode == "query":
        answer, refs = await generate_query_answer(
            storage, req.prompt, chat_history,
            meeting_context=await get_meeting_context(db, req.room_id),
            include_full_storage=True,
        )
        assistant_msg = ChatMessage(
            agent_id=agent_id,
            type="text",
            role="assistant",
            content=answer,
        )
        db.add(assistant_msg)
        await db.commit()

        return AgentRunResponse(answer=answer, referenced_shapes=refs)

    # mode == "generate"
    # Get rejected history
    rej_result = await db.execute(
        select(AgentChange)
        .where(AgentChange.agent_id == agent_id, AgentChange.status == "rejected")
        .order_by(AgentChange.created_at.desc())
        .limit(10)
    )
    rejected = [{"reasoning": r.reasoning, "operations": r.operations} for r in rej_result.scalars()]

    await liveblocks.set_presence(req.room_id, agent_id, "thinking", ttl=30)

    operations, reasoning = await generate_operations(
        storage,
        user_prompt=req.prompt,
        rejected_ops=rejected,
        chat_history=chat_history,
        meeting_context=await get_meeting_context(db, req.room_id),
        request_mode="chat_generate",
        include_full_storage=True,
    )

    # Save assistant text reply
    assistant_msg = ChatMessage(
        agent_id=agent_id,
        type="text",
        role="assistant",
        content=reasoning or "Generated shapes",
    )
    db.add(assistant_msg)

    if not operations:
        await db.commit()
        return AgentRunResponse(
            change_id=None, operations_count=0, reasoning=reasoning
        )

    # Save change to DB
    change_uuid, change_id = _new_change_identity()
    db_change = AgentChange(
        id=change_uuid,
        room_id=req.room_id,
        agent_id=agent_id,
        status="pending",
        operations=operations,
        reasoning=reasoning,
    )
    db.add(db_change)

    # Build operations summary
    op_types = [op.get("op", "?") for op in operations if isinstance(op, dict)]
    shape_types = []
    for op in operations:
        if isinstance(op, dict) and op.get("shape"):
            shape_types.append(op["shape"].get("type", "?"))
    summary = f"{', '.join(op_types)} ({', '.join(shape_types)})" if shape_types else ", ".join(op_types)

    # Save change entry in chat timeline
    change_msg = ChatMessage(
        agent_id=agent_id,
        type="change",
        change_id=change_id,
        change_status="pending",
        operations_summary=summary,
    )
    db.add(change_msg)
    await db.commit()

    # Write to Liveblocks
    now = datetime.now(timezone.utc).isoformat()
    try:
        await liveblocks.patch_storage(req.room_id, [
            {
                "op": "add",
                "path": f"/pendingChanges/{change_id}",
                "value": {
                    "id": change_id,
                    "agentId": agent_id,
                    "status": "pending",
                    "operations": operations,
                    "reasoning": reasoning,
                    "createdAt": now,
                },
            }
        ])
    except Exception as e:
        logger.error(f"Failed to write pending change: {e}")

    await liveblocks.set_presence(req.room_id, agent_id, "suggested", ttl=10)

    return AgentRunResponse(
        change_id=change_id,
        operations_count=len(operations),
        reasoning=reasoning,
    )


# ───────────────────────────────────────────────────────────────────────
# GET /agent/{agent_id}/messages
# ───────────────────────────────────────────────────────────────────────

@agent_router.get("/agent/{agent_id}/messages", response_model=MessagesResponse)
async def get_messages(
    agent_id: str,
    db: DbDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    result = await db.execute(
        select(ChatMessage)
        .where(ChatMessage.agent_id == agent_id)
        .order_by(ChatMessage.created_at)
        .offset(offset)
        .limit(limit)
    )
    messages = result.scalars().all()

    entries = []
    for m in messages:
        if m.type == "text":
            entries.append(TextEntry(
                id=str(m.id),
                role=m.role or "assistant",
                content=m.content or "",
                created_at=m.created_at.isoformat(),
            ))
        elif m.type == "change":
            entries.append(ChangeEntry(
                id=str(m.id),
                change_id=m.change_id or "",
                change_status=m.change_status or "pending",
                operations_summary=m.operations_summary or "",
                created_at=m.created_at.isoformat(),
            ))

    return MessagesResponse(messages=entries)


# ───────────────────────────────────────────────────────────────────────
# Transcript endpoints — POST/GET/DELETE /rooms/{room_id}/transcript
# ───────────────────────────────────────────────────────────────────────

@transcript_router.post("/rooms/{room_id}/transcript", response_model=TranscriptPostResponse)
async def post_transcript(room_id: str, req: TranscriptPostRequest, db: DbDep):
    """Receive transcript chunks from Chrome extension or manual paste."""
    chunks = [c.model_dump() for c in req.chunks]
    count = await store_chunks(db, room_id, chunks)
    return TranscriptPostResponse(room_id=room_id, stored_count=count)


@transcript_router.get("/rooms/{room_id}/transcript", response_model=TranscriptGetResponse)
async def get_transcript(room_id: str, db: DbDep):
    """Get transcript entries and summary for a room."""
    entries = await get_transcript_entries(db, room_id)
    summary = await get_meeting_context(db, room_id)
    return TranscriptGetResponse(
        room_id=room_id,
        entry_count=len(entries),
        entries=entries,
        summary=summary,
    )


@transcript_router.delete("/rooms/{room_id}/transcript", response_model=TranscriptDeleteResponse)
async def delete_transcript(room_id: str, db: DbDep):
    """Clear all transcript data for a room."""
    count = await clear_transcript(db, room_id)
    return TranscriptDeleteResponse(room_id=room_id, deleted_count=count)
