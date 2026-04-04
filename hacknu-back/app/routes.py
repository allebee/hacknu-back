"""
API route handlers — all 6 endpoints.
"""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
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
)
from app.liveblocks import liveblocks
from app.planner import generate_operations, generate_query_answer

logger = logging.getLogger(__name__)

DbDep = Annotated[AsyncSession, Depends(get_db)]

# ── Routers ────────────────────────────────────────────────────────────

complete_router = APIRouter(tags=["complete"])
agents_router = APIRouter(tags=["agents"])
agent_router = APIRouter(tags=["agent"])


# ───────────────────────────────────────────────────────────────────────
# POST /complete
# ───────────────────────────────────────────────────────────────────────

@complete_router.post("/complete", response_model=CompleteResponse)
async def autocomplete(req: CompleteRequest, db: DbDep):
    """Autocomplete endpoint — triggered after idle. Runs with agent_0."""
    agent_id = f"agent_0_{req.room_id}"

    # Ensure default agent exists
    existing = await db.get(Agent, agent_id)
    if not existing:
        agent = Agent(
            id=agent_id,
            room_id=req.room_id,
            name="Autocomplete Agent",
            type="autocomplete",
            is_default=True,
        )
        db.add(agent)
        await db.commit()

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
    operations, reasoning = await generate_operations(storage, rejected_ops=rejected)

    if not operations:
        await liveblocks.set_presence(req.room_id, agent_id, "idle", ttl=5)
        return CompleteResponse(change_id="", operations_count=0, reasoning=reasoning or "No suggestions")

    # Save to DB
    change_id = f"chg_{uuid.uuid4().hex[:12]}"
    change = AgentChange(
        id=uuid.uuid4(),
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

    # Find the change in DB
    result = await db.execute(
        select(AgentChange).where(
            AgentChange.room_id == req.room_id,
            AgentChange.status == "pending",
        )
    )
    change = None
    for c in result.scalars():
        if req.change_id in str(c.id) or req.change_id == str(c.id):
            change = c
            break

    if req.action == "approve":
        # Move shapes from operations to /shapes
        if change:
            ops_patch = []
            for op_data in change.operations:
                if isinstance(op_data, dict) and op_data.get("op") == "add_shape":
                    shape = op_data.get("shape", {})
                    shape_id = shape.get("id", f"shape:{uuid.uuid4().hex[:8]}")
                    ops_patch.append({
                        "op": "add",
                        "path": f"/shapes/{shape_id}",
                        "value": shape,
                    })
                elif isinstance(op_data, dict) and op_data.get("op") == "delete_shape":
                    sid = op_data.get("shapeId", "")
                    if sid:
                        ops_patch.append({"op": "remove", "path": f"/shapes/{sid}"})

            # Remove from pendingChanges
            ops_patch.append({"op": "remove", "path": f"/pendingChanges/{req.change_id}"})

            try:
                await liveblocks.patch_storage(req.room_id, ops_patch)
            except Exception as e:
                logger.error(f"Approve patch failed: {e}")
                raise HTTPException(status_code=502, detail=str(e))

            change.status = "approved"
            change.resolved_at = datetime.now(timezone.utc)
            await db.commit()

        return CompleteActionResponse(status="approved")

    elif req.action == "reject":
        # Remove from Liveblocks
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
        try:
            await liveblocks.patch_storage(req.room_id, [
                {"op": "remove", "path": f"/pendingChanges/{req.change_id}"}
            ])
        except Exception:
            pass

        if change:
            change.status = "rejected"
            change.user_feedback = req.edit_prompt
            change.resolved_at = datetime.now(timezone.utc)
            await db.commit()

        # Re-run with edit context
        storage = await liveblocks.get_storage(req.room_id)
        operations, reasoning = await generate_operations(
            storage,
            user_prompt=req.edit_prompt,
            rejected_ops=[{"reasoning": change.reasoning if change else "", "operations": change.operations if change else []}],
        )

        new_change_id = f"chg_{uuid.uuid4().hex[:12]}"
        if operations:
            new_change = AgentChange(
                id=uuid.uuid4(),
                room_id=req.room_id,
                agent_id=change.agent_id if change else f"agent_0_{req.room_id}",
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
                        "agentId": change.agent_id if change else f"agent_0_{req.room_id}",
                        "status": "pending",
                        "operations": operations,
                        "reasoning": reasoning,
                        "createdAt": now,
                    },
                }
            ])

        return CompleteActionResponse(status="edited", new_change_id=new_change_id)

    raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")


# ───────────────────────────────────────────────────────────────────────
# GET /agents/{room_id}
# ───────────────────────────────────────────────────────────────────────

@agents_router.get("/agents/{room_id}", response_model=ListAgentsResponse)
async def list_agents(room_id: str, db: DbDep):
    result = await db.execute(
        select(Agent).where(Agent.room_id == room_id).order_by(Agent.created_at)
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
    try:
        await liveblocks.patch_storage(room_id, [
            {
                "op": "add",
                "path": f"/agents/{agent_id}",
                "value": {
                    "id": agent_id,
                    "name": req.name,
                    "type": req.type,
                    "isDefault": False,
                    "createdAt": now.isoformat(),
                },
            }
        ])
    except Exception as e:
        logger.warning(f"Failed to write agent to storage: {e}")

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
        .where(ChatMessage.agent_id == agent_id, ChatMessage.type == "text")
        .order_by(ChatMessage.created_at.desc())
        .limit(20)
    )
    chat_history = [
        {"role": m.role, "content": m.content}
        for m in reversed(result.scalars().all())
    ]

    # Save user message
    user_msg = ChatMessage(
        agent_id=agent_id,
        type="text",
        role="user",
        content=req.prompt,
    )
    db.add(user_msg)

    if req.mode == "query":
        answer, refs = await generate_query_answer(storage, req.prompt, chat_history)
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
    change_id = f"chg_{uuid.uuid4().hex[:12]}"
    db_change = AgentChange(
        id=uuid.uuid4(),
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
