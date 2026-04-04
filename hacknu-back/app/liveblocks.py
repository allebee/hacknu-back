"""
Liveblocks REST API client for reading/writing storage and presence.
"""

from __future__ import annotations

import httpx

from app.config import LIVEBLOCKS_SECRET_KEY


class LiveblocksClient:
    BASE = "https://api.liveblocks.io/v2"

    def __init__(self, secret_key: str | None = None):
        self._key = secret_key or LIVEBLOCKS_SECRET_KEY
        self._headers = {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    # ── Storage ────────────────────────────────────────────────────────

    async def get_storage(self, room_id: str) -> dict:
        """GET /v2/rooms/{roomId}/storage?format=json"""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                f"{self.BASE}/rooms/{room_id}/storage",
                params={"format": "json"},
                headers=self._headers,
            )
            r.raise_for_status()
            return r.json()

    async def patch_storage(self, room_id: str, operations: list[dict]) -> None:
        """PATCH /v2/rooms/{roomId}/storage/json-patch (RFC 6902)"""
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.patch(
                f"{self.BASE}/rooms/{room_id}/storage/json-patch",
                json=operations,
                headers=self._headers,
            )
            r.raise_for_status()

    # ── Presence ───────────────────────────────────────────────────────

    async def set_presence(
        self,
        room_id: str,
        agent_id: str,
        status: str,
        ttl: int = 30,
    ) -> None:
        """POST /v2/rooms/{roomId}/presence"""
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{self.BASE}/rooms/{room_id}/presence",
                json={
                    "userId": agent_id,
                    "data": {"status": status},
                    "userInfo": {"name": f"Agent {agent_id}", "avatar": ""},
                    "ttl": ttl,
                },
                headers=self._headers,
            )

    # ── Room management ────────────────────────────────────────────────

    async def create_room(self, room_id: str) -> dict:
        """POST /v2/rooms?idempotent — get or create."""
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self.BASE}/rooms",
                params={"idempotent": "true"},
                json={
                    "id": room_id,
                    "defaultAccesses": ["room:write"],
                },
                headers=self._headers,
            )
            r.raise_for_status()
            return r.json()

    async def initialize_storage(self, room_id: str, data: dict) -> None:
        """POST /v2/rooms/{roomId}/storage — initialize empty storage."""
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"{self.BASE}/rooms/{room_id}/storage",
                json=data,
                headers=self._headers,
            )
            # 409 means storage already exists — that's fine
            if r.status_code != 409:
                r.raise_for_status()


# Singleton
liveblocks = LiveblocksClient()
