from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from app.planner import _build_context, generate_operations, generate_query_answer


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
        self.assertIn("Only include keys relevant to the chosen shape type", system_prompt)
        self.assertIn("align it by exact `x` or exact `y`", system_prompt)
        self.assertIn("Never place a new shape diagonally", system_prompt)
        self.assertIn("anchor the new shape or change to the newest relevant shape", system_prompt)
        self.assertIn("default to the relevant shape with the largest `eventTs`", system_prompt)
        self.assertIn("recency takes priority over insertion order", system_prompt)
        self.assertIn("do not create a duplicate arrow between the same two shape IDs", system_prompt)
        self.assertIn("existing arrow between the same two shapes in either direction", system_prompt)
        self.assertIn("Allow at most one arrow between any two shape IDs", system_prompt)
        self.assertIn("do not create `B -> A` as a separate back arrow", system_prompt)
        self.assertIn("preserve that same linkage logic for any newly added significant item", system_prompt)
        self.assertIn("include the matching arrow connection for the newly added significant shape", system_prompt)
        self.assertIn("exactly 1 significant non-arrow canvas change", system_prompt)
        self.assertIn("Do not make standalone arrow-only", system_prompt)
        self.assertNotIn('"color": "blue"', system_prompt)
        self.assertNotIn('"fill": "solid"', system_prompt)
        self.assertNotIn('"size": "m"', system_prompt)

    async def test_chat_generate_uses_chat_prompt(self) -> None:
        create = AsyncMock(return_value=_fake_llm_response('{"reasoning":"Hello","draft_operations":[]}'))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.planner._get_client", return_value=(fake_client, "gpt-test")):
            await generate_operations({"shapes": {}}, user_prompt="hello", request_mode="chat_generate")

        system_prompt = create.await_args.kwargs["messages"][0]["content"]
        self.assertIn("Default to chat-only responses", system_prompt)
        self.assertIn("exactly 1 significant non-arrow canvas change", system_prompt)
        self.assertIn("Do not use a standalone arrow", system_prompt)
        self.assertIn("answer at a high level for a human reader", system_prompt)
        self.assertIn("Do not mention exact coordinates, dimensions, shape IDs, or exact item counts unless the user explicitly asks", system_prompt)
        self.assertNotIn("proactive, but not pushy", system_prompt)

    async def test_edit_mode_uses_revision_prompt(self) -> None:
        create = AsyncMock(return_value=_fake_llm_response('{"reasoning":"Updated","draft_operations":[]}'))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.planner._get_client", return_value=(fake_client, "gpt-test")):
            await generate_operations({"shapes": {}}, user_prompt="move it right", request_mode="edit_suggestion")

        system_prompt = create.await_args.kwargs["messages"][0]["content"]
        self.assertIn("revising a previous pending canvas suggestion", system_prompt)
        self.assertIn("Favor modifying or reconnecting existing suggested content", system_prompt)

    async def test_query_prompt_discourages_low_level_canvas_dump(self) -> None:
        create = AsyncMock(return_value=_fake_llm_response('{"answer":"High-level summary","referenced_shapes":[]}'))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.planner._get_client", return_value=(fake_client, "gpt-test")):
            await generate_query_answer({"shapes": {}}, user_prompt="Explain this canvas")

        system_prompt = create.await_args.kwargs["messages"][0]["content"]
        self.assertIn("answer at the user's level of abstraction", system_prompt)
        self.assertIn("Do not mention exact coordinates, dimensions, shape IDs, or exact item counts unless the user explicitly asks", system_prompt)

    def test_build_context_compacts_notes_for_llm(self) -> None:
        storage = {
            "shapes": {
                "shape:note": {
                    "id": "shape:note",
                    "type": "note",
                    "x": 100,
                    "y": 120,
                    "rotation": 0,
                    "index": "a1",
                    "parentId": "page:page",
                    "isLocked": False,
                    "opacity": 1,
                    "meta": {},
                    "props": {
                        "color": "yellow",
                        "labelColor": "black",
                        "size": "m",
                        "font": "sans",
                        "fontSizeAdjustment": 0,
                        "align": "middle",
                        "verticalAlign": "middle",
                        "growY": 0,
                        "url": "",
                        "richText": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Lean note payload"}],
                                }
                            ],
                        },
                        "scale": 1,
                    },
                },
                "shape:geo": {
                    "id": "shape:geo",
                    "type": "geo",
                    "x": 260,
                    "y": 120,
                    "props": {
                        "geo": "diamond",
                        "w": 220,
                        "h": 120,
                        "color": "red",
                        "font": "sans",
                        "richText": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Decision"}],
                                }
                            ],
                        },
                    },
                },
                "shape:text": {
                    "id": "shape:text",
                    "type": "text",
                    "x": 520,
                    "y": 120,
                    "props": {
                        "color": "green",
                        "font": "sans",
                        "w": 420,
                        "richText": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Standalone text"}],
                                }
                            ],
                        },
                    },
                },
                "shape:frame": {
                    "id": "shape:frame",
                    "type": "frame",
                    "x": 40,
                    "y": 40,
                    "props": {
                        "w": 900,
                        "h": 420,
                        "name": "Working area",
                        "color": "blue",
                    },
                },
                "shape:arrow": {
                    "id": "shape:arrow",
                    "type": "arrow",
                    "x": 0,
                    "y": 0,
                    "meta": {
                        "agentConnection": {
                            "startShapeId": "shape:note",
                            "endShapeId": "shape:geo",
                        }
                    },
                    "props": {
                        "color": "orange",
                        "font": "sans",
                        "start": {"x": 0, "y": 0},
                        "end": {"x": 100, "y": 100},
                        "richText": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "connects"}],
                                }
                            ],
                        },
                    },
                },
                "shape:draw": {
                    "id": "shape:draw",
                    "type": "draw",
                    "x": 0,
                    "y": 0,
                    "props": {"segments": [{"type": "free", "points": [{"x": 0, "y": 0, "z": 0.5}]}]},
                },
                "shape:group": {
                    "id": "shape:group",
                    "type": "group",
                    "x": 0,
                    "y": 0,
                    "props": {},
                },
            },
            "pendingChanges": {
                "change:1": {
                    "id": "change:1",
                    "agentId": "agent:test",
                    "status": "pending",
                    "reasoning": "Add a note",
                    "operations": [
                        {
                            "op": "add_shape",
                            "shape": {
                                "id": "shape:pending",
                                "type": "note",
                                "x": 300,
                                "y": 200,
                                "props": {
                                    "color": "blue",
                                    "labelColor": "black",
                                    "size": "m",
                                    "font": "sans",
                                    "richText": {
                                        "type": "doc",
                                        "content": [
                                            {
                                                "type": "paragraph",
                                                "content": [{"type": "text", "text": "Pending note"}],
                                            }
                                        ],
                                    },
                                },
                            },
                        }
                    ],
                }
            },
        }

        context = _build_context(storage, include_full_storage=True)

        self.assertIn("CURRENT CANVAS (5 shapes):", context)
        self.assertIn('[note] id=shape:note at (100,120) text="Lean note payload"', context)
        self.assertIn('[geo] id=shape:geo at (260,120) geo=diamond size=220x120 text="Decision"', context)
        self.assertIn('[text] id=shape:text at (520,120) text="Standalone text"', context)
        self.assertIn('[frame] id=shape:frame at (40,40) size=900x420 name="Working area"', context)
        self.assertIn('[arrow] id=shape:arrow at (0,0) links=shape:note->shape:geo text="connects"', context)
        self.assertIn('"shape:note":{"id":"shape:note","type":"note","x":100,"y":120,"text":"Lean note payload"}', context)
        self.assertIn('"shape:geo":{"id":"shape:geo","type":"geo","x":260,"y":120,"geo":"diamond","w":220,"h":120,"text":"Decision"}', context)
        self.assertIn('"shape:text":{"id":"shape:text","type":"text","x":520,"y":120,"text":"Standalone text"}', context)
        self.assertIn('"shape:frame":{"id":"shape:frame","type":"frame","x":40,"y":40,"w":900,"h":420,"name":"Working area"}', context)
        self.assertIn('"shape:arrow":{"id":"shape:arrow","type":"arrow","x":0,"y":0,"startShapeId":"shape:note","endShapeId":"shape:geo","text":"connects"}', context)
        self.assertIn('"shape":{"id":"shape:pending","type":"note","x":300,"y":200,"text":"Pending note"}', context)
        self.assertNotIn("shape:draw", context)
        self.assertNotIn("shape:group", context)
        self.assertNotIn('"props"', context)
        self.assertNotIn('"font":"sans"', context)
        self.assertNotIn('"color":"red"', context)
        self.assertNotIn('"color":"green"', context)
        self.assertNotIn('"labelColor":"black"', context)

    def test_build_context_orders_shapes_by_event_ts_for_llm(self) -> None:
        storage = {
            "shapes": {
                "shape:first": {
                    "id": "shape:first",
                    "type": "note",
                    "x": 100,
                    "y": 100,
                    "rotation": 0,
                    "index": "a1",
                    "parentId": "page:page",
                    "isLocked": False,
                    "opacity": 1,
                    "meta": {"event_ts": "2026-04-05T09:00:00Z"},
                    "props": {
                        "color": "yellow",
                        "labelColor": "black",
                        "size": "m",
                        "font": "sans",
                        "fontSizeAdjustment": 0,
                        "align": "middle",
                        "verticalAlign": "middle",
                        "growY": 0,
                        "url": "",
                        "richText": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "First sticker"}],
                                }
                            ],
                        },
                        "scale": 1,
                    },
                },
                "shape:latest": {
                    "id": "shape:latest",
                    "type": "note",
                    "x": 340,
                    "y": 100,
                    "rotation": 0,
                    "index": "a2",
                    "parentId": "page:page",
                    "isLocked": False,
                    "opacity": 1,
                    "meta": {"event_ts": 1775386800000},
                    "props": {
                        "color": "yellow",
                        "labelColor": "black",
                        "size": "m",
                        "font": "sans",
                        "fontSizeAdjustment": 0,
                        "align": "middle",
                        "verticalAlign": "middle",
                        "growY": 0,
                        "url": "",
                        "richText": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Latest sticker"}],
                                }
                            ],
                        },
                        "scale": 1,
                    },
                },
                "shape:middle": {
                    "id": "shape:middle",
                    "type": "note",
                    "x": 220,
                    "y": 100,
                    "rotation": 0,
                    "index": "a3",
                    "parentId": "page:page",
                    "isLocked": False,
                    "opacity": 1,
                    "meta": {"event_ts": "2026-04-05T10:00:00Z"},
                    "props": {
                        "color": "yellow",
                        "labelColor": "black",
                        "size": "m",
                        "font": "sans",
                        "fontSizeAdjustment": 0,
                        "align": "middle",
                        "verticalAlign": "middle",
                        "growY": 0,
                        "url": "",
                        "richText": {
                            "type": "doc",
                            "content": [
                                {
                                    "type": "paragraph",
                                    "content": [{"type": "text", "text": "Middle sticker"}],
                                }
                            ],
                        },
                        "scale": 1,
                    },
                },
            }
        }

        context = _build_context(storage, include_full_storage=True)

        summary_latest = '[note] id=shape:latest at (340,100) eventTs=2026-04-05T11:00:00Z text="Latest sticker"'
        summary_middle = '[note] id=shape:middle at (220,100) eventTs=2026-04-05T10:00:00Z text="Middle sticker"'
        summary_first = '[note] id=shape:first at (100,100) eventTs=2026-04-05T09:00:00Z text="First sticker"'
        self.assertIn(
            "RECENCY PRIORITY: when several shapes could match, prefer the newest relevant shape by eventTs.",
            context,
        )
        self.assertIn("NEWEST TIMESTAMPED SHAPES FIRST:", context)
        self.assertLess(context.index(summary_middle), context.index(summary_first))
        self.assertLess(context.index(summary_latest), context.index(summary_middle))
        self.assertIn('"shape:latest":{"id":"shape:latest","type":"note","x":340,"y":100,"eventTs":"2026-04-05T11:00:00Z","text":"Latest sticker"}', context)
        self.assertLess(
            context.index('"shape:latest":{"id":"shape:latest","type":"note","x":340,"y":100,"eventTs":"2026-04-05T11:00:00Z","text":"Latest sticker"}'),
            context.index('"shape:first":{"id":"shape:first","type":"note","x":100,"y":100,"eventTs":"2026-04-05T09:00:00Z","text":"First sticker"}'),
        )

    def test_build_context_includes_viewport(self) -> None:
        context = _build_context(
            {"shapes": {}},
            viewport={"x": 10, "y": 20, "width": 800, "height": 600, "zoom": 1.25},
        )

        self.assertIn(
            "CURRENT VIEWPORT: x=10, y=20, width=800, height=600, right=810, bottom=620, zoom=1.25",
            context,
        )

    async def test_generate_operations_passes_viewport_to_layout_pipeline(self) -> None:
        create = AsyncMock(return_value=_fake_llm_response('{"reasoning":"Placed","draft_operations":[]}'))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        viewport = {"x": 25, "y": 50, "width": 900, "height": 700}

        with (
            patch("app.planner._get_client", return_value=(fake_client, "gpt-test")),
            patch("app.planner.compile_draft_operations", return_value=[]) as compile_mock,
            patch("app.planner.normalize_generated_operations", return_value=[]) as normalize_mock,
        ):
            await generate_operations(
                {"shapes": {}},
                user_prompt="add a note",
                request_mode="chat_generate",
                viewport=viewport,
            )

        compile_mock.assert_called_once_with({"shapes": {}}, [], viewport=viewport)
        normalize_mock.assert_called_once_with({"shapes": {}}, [], viewport=viewport)

    async def test_generate_operations_salvages_concatenated_json_objects(self) -> None:
        payload = (
            '{"reasoning":"Placed","draft_operations":[]}\n'
            '{"reasoning":"Placed","draft_operations":[]}'
        )
        create = AsyncMock(return_value=_fake_llm_response(payload))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with (
            patch("app.planner._get_client", return_value=(fake_client, "gpt-test")),
            patch("app.planner.compile_draft_operations", return_value=[]) as compile_mock,
            patch("app.planner.normalize_generated_operations", return_value=[]) as normalize_mock,
        ):
            operations, reasoning = await generate_operations({"shapes": {}}, user_prompt="add a sticker")

        self.assertEqual(operations, [])
        self.assertEqual(reasoning, "Placed")
        compile_mock.assert_called_once_with({"shapes": {}}, [], viewport=None)
        normalize_mock.assert_called_once_with({"shapes": {}}, [], viewport=None)

    async def test_generate_query_answer_salvages_concatenated_json_objects(self) -> None:
        payload = (
            '{"answer":"Summary","referenced_shapes":["shape:1"]}\n'
            '{"answer":"Summary","referenced_shapes":["shape:1"]}'
        )
        create = AsyncMock(return_value=_fake_llm_response(payload))
        fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

        with patch("app.planner._get_client", return_value=(fake_client, "gpt-test")):
            answer, refs = await generate_query_answer({"shapes": {}}, user_prompt="what is this?")

        self.assertEqual(answer, "Summary")
        self.assertEqual(refs, ["shape:1"])
