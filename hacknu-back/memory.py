"""
Simple in-memory session store for agent context.

Stores recent context snapshots and agent actions per room,
so the agent can maintain continuity within a brainstorm session.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
import time


@dataclass
class SessionEntry:
    """A single agent interaction record."""
    timestamp: float
    mode: str
    user_intent: str
    actions_returned: list[dict]
    reasoning: str


@dataclass
class RoomSession:
    """Session state for a single Liveblocks room."""
    room_id: str
    history: list[SessionEntry] = field(default_factory=list)
    max_history: int = 20

    def add_entry(self, mode: str, user_intent: str, actions: list[dict], reasoning: str):
        entry = SessionEntry(
            timestamp=time.time(),
            mode=mode,
            user_intent=user_intent,
            actions_returned=actions,
            reasoning=reasoning,
        )
        self.history.append(entry)
        # Keep only last N entries
        if len(self.history) > self.max_history:
            self.history = self.history[-self.max_history:]

    def get_summary(self, last_n: int = 5) -> str:
        """Get a text summary of recent agent interactions for context."""
        if not self.history:
            return "No previous agent interactions in this session."

        entries = self.history[-last_n:]
        lines = []
        for e in entries:
            action_types = [a.get("action_type", "?") for a in e.actions_returned]
            lines.append(
                f"- [{e.mode}] Intent: \"{e.user_intent}\" → "
                f"Actions: {action_types} | Reasoning: {e.reasoning}"
            )
        return "Recent agent history:\n" + "\n".join(lines)


class SessionStore:
    """Global in-memory store for all room sessions."""

    def __init__(self):
        self._rooms: dict[str, RoomSession] = {}

    def get_session(self, room_id: str) -> RoomSession:
        if room_id not in self._rooms:
            self._rooms[room_id] = RoomSession(room_id=room_id)
        return self._rooms[room_id]

    def clear_session(self, room_id: str):
        self._rooms.pop(room_id, None)


# Singleton
store = SessionStore()
