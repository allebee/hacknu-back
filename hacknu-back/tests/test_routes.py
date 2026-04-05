from __future__ import annotations

import uuid
from datetime import datetime, timezone
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.models import Agent, AgentChange
from app.operations import compile_draft_operations
from app.routes import _committed_operations, agent_action
from app.schemas import AgentActionRequest


class _FakeScalars:
    def __init__(self, items):
        self._items = list(items)

    def all(self):
        return list(self._items)

    def __iter__(self):
        return iter(self._items)


class _FakeExecuteResult:
    def __init__(self, items):
        self._items = list(items)

    def scalars(self):
        return _FakeScalars(self._items)


class _FakeSession:
    def __init__(self, *, agent: Agent, pending_rows: list[AgentChange], chat_rows: list | None = None):
        self.agent = agent
        self.pending_rows = list(pending_rows)
        self.chat_rows = list(chat_rows or [])
        self.commit_count = 0

    async def get(self, model, key):
        if model is Agent and key == self.agent.id:
            return self.agent
        return None

    async def execute(self, stmt):
        stmt_text = str(stmt)
        if "FROM agent_changes" in stmt_text:
            return _FakeExecuteResult(self.pending_rows)
        if "FROM chat_messages" in stmt_text:
            return _FakeExecuteResult(self.chat_rows)
        raise AssertionError(f"Unexpected SQL statement: {stmt_text}")

    def add(self, obj):
        return None

    async def commit(self):
        self.commit_count += 1


class RouteHelperTests(unittest.TestCase):
    def test_committed_operations_recolor_added_notes_without_mutating_source(self) -> None:
        pending_note = compile_draft_operations(
            {"shapes": {}},
            [{"op": "add_shape", "shape": {"type": "note", "label": "Approved note"}}],
        )[0]["shape"]
        source_operations = [{"op": "add_shape", "shape": pending_note}]

        committed_operations = _committed_operations(source_operations)

        self.assertEqual(source_operations[0]["shape"]["props"]["color"], "blue")
        self.assertEqual(committed_operations[0]["shape"]["props"]["color"], "yellow")
        self.assertEqual(
            committed_operations[0]["shape"]["props"]["richText"],
            source_operations[0]["shape"]["props"]["richText"],
        )


class AgentActionRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_agent_action_approve_commits_pending_change_for_that_agent(self) -> None:
        agent = Agent(
            id="agent_chat_room-1",
            room_id="room-1",
            name="Chat Agent",
            type="chatbot",
            is_default=False,
            created_at=datetime.now(timezone.utc),
        )
        change_uuid = uuid.uuid4()
        change_id = str(change_uuid)
        pending_note = compile_draft_operations(
            {"shapes": {}},
            [{"op": "add_shape", "shape": {"type": "note", "label": "Approve me"}}],
        )[0]["shape"]
        pending_change = AgentChange(
            id=change_uuid,
            room_id=agent.room_id,
            agent_id=agent.id,
            status="pending",
            operations=[{"op": "add_shape", "shape": pending_note}],
            reasoning="Add a note",
            x=12.0,
            y=24.0,
            created_at=datetime.now(timezone.utc),
        )
        storage = {
            "shapes": {},
            "pendingChanges": {
                change_id: {
                    "id": change_id,
                    "agentId": agent.id,
                    "status": "pending",
                    "operations": [{"op": "add_shape", "shape": pending_note}],
                    "reasoning": "Add a note",
                }
            },
        }
        req = AgentActionRequest(change_id=change_id, action="approve")
        db = _FakeSession(agent=agent, pending_rows=[pending_change])

        with patch("app.routes.liveblocks.get_storage", AsyncMock(return_value=storage)), patch(
            "app.routes.liveblocks.patch_storage",
            AsyncMock(),
        ) as patch_storage:
            response = await agent_action(agent.id, req, db)

        self.assertEqual(response.status, "approved")
        self.assertEqual(pending_change.status, "approved")
        patch_storage.assert_awaited_once()
        patch_room_id, patch_payload = patch_storage.await_args.args
        self.assertEqual(patch_room_id, agent.room_id)
        self.assertEqual(patch_payload[-1], {"op": "remove", "path": f"/pendingChanges/{change_id}"})
        self.assertEqual(db.commit_count, 1)

    async def test_agent_action_rejects_pending_change_from_other_agent(self) -> None:
        agent = Agent(
            id="agent_chat_room-1",
            room_id="room-1",
            name="Chat Agent",
            type="chatbot",
            is_default=False,
            created_at=datetime.now(timezone.utc),
        )
        change_id = str(uuid.uuid4())
        storage = {
            "shapes": {},
            "pendingChanges": {
                change_id: {
                    "id": change_id,
                    "agentId": "agent_other_room-1",
                    "status": "pending",
                    "operations": [],
                    "reasoning": "Other agent suggestion",
                }
            },
        }
        req = AgentActionRequest(change_id=change_id, action="reject")
        db = _FakeSession(agent=agent, pending_rows=[])

        with patch("app.routes.liveblocks.get_storage", AsyncMock(return_value=storage)):
            with self.assertRaises(HTTPException) as ctx:
                await agent_action(agent.id, req, db)

        self.assertEqual(ctx.exception.status_code, 404)


if __name__ == "__main__":
    unittest.main()
