from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.planner import generate_operations


def _fake_llm_response(payload: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=payload))]
    )


class PlannerModePromptTests(unittest.IsolatedAsyncioTestCase):
    async def test_autocomplete_uses_autocomplete_prompt(self) -> None:
        create = AsyncMock(return_value=_fake_llm_response('{"reasoning":"No change","draft_operations":[]}'))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.planner._get_client", return_value=(fake_client, "gpt-test")):
            await generate_operations({"shapes": {}}, request_mode="autocomplete")

        system_prompt = create.await_args.kwargs["messages"][0]["content"]
        self.assertIn("proactive, but not pushy", system_prompt)
        self.assertIn('"draft_operations"', system_prompt)

    async def test_chat_generate_uses_chat_prompt(self) -> None:
        create = AsyncMock(return_value=_fake_llm_response('{"reasoning":"Hello","draft_operations":[]}'))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.planner._get_client", return_value=(fake_client, "gpt-test")):
            await generate_operations({"shapes": {}}, user_prompt="hello", request_mode="chat_generate")

        system_prompt = create.await_args.kwargs["messages"][0]["content"]
        self.assertIn("Default to chat-only responses", system_prompt)
        self.assertNotIn("proactive, but not pushy", system_prompt)

    async def test_edit_mode_uses_revision_prompt(self) -> None:
        create = AsyncMock(return_value=_fake_llm_response('{"reasoning":"Updated","draft_operations":[]}'))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.planner._get_client", return_value=(fake_client, "gpt-test")):
            await generate_operations({"shapes": {}}, user_prompt="move it right", request_mode="edit_suggestion")

        system_prompt = create.await_args.kwargs["messages"][0]["content"]
        self.assertIn("revising a previous pending canvas suggestion", system_prompt)
        self.assertIn("Favor modifying or reconnecting existing suggested content", system_prompt)
