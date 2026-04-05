from __future__ import annotations

import unittest

from app.schemas import AgentActionRequest, AgentRunRequest, CompleteActionRequest, CompleteRequest


class ViewportSchemaTests(unittest.TestCase):
    def test_complete_request_accepts_viewport_aliases(self) -> None:
        req = CompleteRequest.model_validate(
            {
                "room_id": "room-1",
                "viewport": {"x": 10, "y": 20, "w": 800, "h": 600, "zoom": 1.25},
            }
        )

        assert req.viewport is not None
        self.assertEqual(req.viewport.width, 800)
        self.assertEqual(req.viewport.height, 600)
        self.assertEqual(req.viewport.zoom, 1.25)

    def test_complete_action_request_accepts_viewport(self) -> None:
        req = CompleteActionRequest.model_validate(
            {
                "room_id": "room-1",
                "change_id": "change-1",
                "action": "edit",
                "edit_prompt": "move it down",
                "viewport": {"width": 1024, "height": 768},
            }
        )

        assert req.viewport is not None
        self.assertEqual(req.viewport.width, 1024)
        self.assertEqual(req.viewport.height, 768)

    def test_agent_run_request_accepts_viewport(self) -> None:
        req = AgentRunRequest.model_validate(
            {
                "room_id": "room-1",
                "prompt": "add a note",
                "mode": "generate",
                "viewport": {"width": 1440, "height": 900},
            }
        )

        assert req.viewport is not None
        self.assertEqual(req.viewport.width, 1440)
        self.assertEqual(req.viewport.height, 900)

    def test_agent_action_request_accepts_optional_room_id_and_viewport(self) -> None:
        req = AgentActionRequest.model_validate(
            {
                "room_id": "room-1",
                "change_id": "change-1",
                "action": "approve",
                "viewport": {"w": 1280, "h": 720},
            }
        )

        self.assertEqual(req.room_id, "room-1")
        assert req.viewport is not None
        self.assertEqual(req.viewport.width, 1280)
        self.assertEqual(req.viewport.height, 720)


if __name__ == "__main__":
    unittest.main()
