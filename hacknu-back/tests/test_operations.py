from __future__ import annotations

import unittest

from app.operations import (
    AGENT_CONNECTION_META_KEY,
    compile_draft_operations,
    get_change_cursor,
    normalize_generated_operations,
    prepare_shape_for_commit,
    sanitize_operations_for_apply,
)
from app.shapes import DEFAULT_GEO_HEIGHT, DEFAULT_GEO_WIDTH


class OperationNormalizationTests(unittest.TestCase):
    def test_compiles_semantic_note_with_backend_defaults(self) -> None:
        compiled = compile_draft_operations(
            {"shapes": {}},
            [
                {
                    "op": "add_shape",
                    "ref": "next_step",
                    "shape": {
                        "type": "note",
                        "label": "Capture risks",
                        "color": "green",
                        "font": "mono",
                        "size": "xl",
                    },
                }
            ],
        )

        shape = compiled[0]["shape"]

        self.assertTrue(shape["id"].startswith("shape:"))
        self.assertEqual(shape["index"], "a1")
        self.assertEqual(shape["parentId"], "page:page")
        self.assertEqual(shape["props"]["color"], "blue")
        self.assertEqual(shape["props"]["font"], "sans")
        self.assertEqual(shape["props"]["size"], "m")
        self.assertEqual(shape["props"]["labelColor"], "black")
        self.assertEqual(
            shape["props"]["richText"],
            {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Capture risks"}]}]},
        )

    def test_compiles_text_with_hardcoded_font(self) -> None:
        compiled = compile_draft_operations(
            {"shapes": {}},
            [
                {
                    "op": "add_shape",
                    "shape": {
                        "type": "text",
                        "label": "Shared plan",
                        "color": "green",
                        "size": "xl",
                        "font": "mono",
                        "w": 480,
                        "autoSize": False,
                    },
                }
            ],
        )

        shape = compiled[0]["shape"]
        self.assertEqual(shape["props"]["font"], "sans")
        self.assertEqual(shape["props"]["color"], "black")
        self.assertEqual(shape["props"]["size"], "m")
        self.assertEqual(shape["props"]["textAlign"], "start")
        self.assertEqual(shape["props"]["w"], 200.0)
        self.assertTrue(shape["props"]["autoSize"])

    def test_compiles_geo_with_hardcoded_style_defaults(self) -> None:
        compiled = compile_draft_operations(
            {"shapes": {}},
            [
                {
                    "op": "add_shape",
                    "shape": {
                        "type": "geo",
                        "geo": "diamond",
                        "label": "Decision",
                        "color": "red",
                        "fill": "pattern",
                        "size": "xl",
                        "font": "mono",
                        "w": 260,
                        "h": 140,
                    },
                }
            ],
        )

        shape = compiled[0]["shape"]
        self.assertEqual(shape["props"]["geo"], "diamond")
        self.assertEqual(shape["props"]["w"], 260.0)
        self.assertEqual(shape["props"]["h"], 140.0)
        self.assertEqual(shape["props"]["color"], "black")
        self.assertEqual(shape["props"]["fill"], "solid")
        self.assertEqual(shape["props"]["size"], "m")
        self.assertEqual(shape["props"]["font"], "sans")
        self.assertEqual(shape["props"]["labelColor"], "black")

    def test_compiles_geo_with_larger_default_size(self) -> None:
        compiled = compile_draft_operations(
            {"shapes": {}},
            [
                {
                    "op": "add_shape",
                    "shape": {
                        "type": "geo",
                        "geo": "rectangle",
                        "label": "Longer label that needs room",
                    },
                }
            ],
        )

        shape = compiled[0]["shape"]
        self.assertEqual(shape["props"]["w"], float(DEFAULT_GEO_WIDTH))
        self.assertEqual(shape["props"]["h"], float(DEFAULT_GEO_HEIGHT))

    def test_compiles_frame_with_hardcoded_color(self) -> None:
        compiled = compile_draft_operations(
            {"shapes": {}},
            [
                {
                    "op": "add_shape",
                    "shape": {
                        "type": "frame",
                        "name": "Cluster",
                        "color": "blue",
                        "w": 640,
                        "h": 320,
                    },
                }
            ],
        )

        shape = compiled[0]["shape"]
        self.assertEqual(shape["props"]["name"], "Cluster")
        self.assertEqual(shape["props"]["w"], 640.0)
        self.assertEqual(shape["props"]["h"], 320.0)
        self.assertEqual(shape["props"]["color"], "black")

    def test_prepare_shape_for_commit_recolors_pending_note(self) -> None:
        pending_note = compile_draft_operations(
            {"shapes": {}},
            [{"op": "add_shape", "shape": {"type": "note", "label": "Ship v1"}}],
        )[0]["shape"]

        committed_note = prepare_shape_for_commit(pending_note)

        self.assertEqual(pending_note["props"]["color"], "blue")
        self.assertEqual(committed_note["props"]["color"], "yellow")
        self.assertEqual(committed_note["props"]["font"], "sans")
        self.assertEqual(
            committed_note["props"]["richText"],
            {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Ship v1"}]}]},
        )

    def test_compiles_arrow_from_local_refs(self) -> None:
        compiled = compile_draft_operations(
            {"shapes": {}},
            [
                {
                    "op": "add_shape",
                    "ref": "left",
                    "shape": {
                        "type": "note",
                        "label": "Start",
                        "x": 100,
                        "y": 100,
                    },
                },
                {
                    "op": "add_shape",
                    "ref": "right",
                    "shape": {
                        "type": "note",
                        "label": "Finish",
                        "x": 500,
                        "y": 100,
                    },
                },
                {
                    "op": "add_shape",
                    "shape": {
                        "type": "arrow",
                        "label": "flows",
                        "color": "red",
                        "size": "xl",
                        "kind": "elbow",
                        "startShapeId": "ref:left",
                        "endShapeId": "ref:right",
                    },
                },
            ],
        )

        normalized = normalize_generated_operations({"shapes": {}}, compiled)
        left_id = normalized[0]["shape"]["id"]
        right_id = normalized[1]["shape"]["id"]
        arrow = normalized[2]["shape"]

        self.assertEqual(arrow["meta"][AGENT_CONNECTION_META_KEY]["startShapeId"], left_id)
        self.assertEqual(arrow["meta"][AGENT_CONNECTION_META_KEY]["endShapeId"], right_id)
        self.assertEqual(arrow["props"]["color"], "black")
        self.assertEqual(arrow["props"]["size"], "m")
        self.assertEqual(arrow["props"]["kind"], "arc")
        self.assertEqual(arrow["props"]["font"], "sans")
        self.assertEqual(arrow["props"]["richText"]["content"][0]["content"][0]["text"], "flows")

    def test_reflows_new_note_away_from_existing_note(self) -> None:
        storage = {
            "shapes": {
                "shape:existing": {
                    "id": "shape:existing",
                    "type": "note",
                    "x": 100,
                    "y": 100,
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
                        "richText": {"type": "doc", "content": []},
                        "scale": 1,
                    },
                }
            }
        }
        operations = [
            {
                "op": "add_shape",
                "shape": {
                    "id": "shape:new",
                    "type": "note",
                    "x": 110,
                    "y": 110,
                    "rotation": 0,
                    "index": "a2",
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
                        "richText": {"type": "doc", "content": []},
                        "scale": 1,
                    },
                },
            }
        ]

        normalized = normalize_generated_operations(storage, operations)
        new_shape = normalized[0]["shape"]

        self.assertTrue(
            abs(new_shape["x"] - 100) >= 240 or abs(new_shape["y"] - 100) >= 240
        )

    def test_compile_wraps_new_shape_inside_viewport(self) -> None:
        storage = {"shapes": {"shape:existing": _note("shape:existing", 100, 100)}}

        compiled = compile_draft_operations(
            storage,
            [{"op": "add_shape", "shape": {"type": "geo", "label": "Next step"}}],
            viewport={"x": 0, "y": 0, "width": 500, "height": 600},
        )

        shape = compiled[0]["shape"]
        self.assertEqual(shape["x"], 120.0)
        self.assertEqual(shape["y"], 340.0)

    def test_normalize_reflows_shape_back_into_viewport(self) -> None:
        operations = [{"op": "add_shape", "shape": _note("shape:new", 400, 100)}]

        normalized = normalize_generated_operations(
            {"shapes": {}},
            operations,
            viewport={"x": 0, "y": 0, "width": 500, "height": 500},
        )

        shape = normalized[0]["shape"]
        self.assertEqual(shape["x"], 300.0)
        self.assertEqual(shape["y"], 100.0)

    def test_normalizes_arrow_from_connection_metadata(self) -> None:
        storage = {
            "shapes": {
                "shape:left": _note("shape:left", 100, 100),
                "shape:right": _note("shape:right", 500, 100),
            }
        }
        operations = [
            {
                "op": "add_shape",
                "shape": {
                    "id": "shape:arrow",
                    "type": "arrow",
                    "x": 0,
                    "y": 0,
                    "rotation": 0,
                    "index": "a3",
                    "parentId": "page:page",
                    "isLocked": False,
                    "opacity": 1,
                    "meta": {
                        AGENT_CONNECTION_META_KEY: {
                            "startShapeId": "shape:left",
                            "endShapeId": "shape:right",
                            "startAnchor": {"x": 0.5, "y": 0.5},
                            "endAnchor": {"x": 0.5, "y": 0.5},
                        }
                    },
                    "props": {
                        "kind": "arc",
                        "start": {"x": 0, "y": 0},
                        "end": {"x": 0, "y": 0},
                        "bend": 0,
                        "color": "black",
                        "fill": "none",
                        "dash": "solid",
                        "size": "m",
                        "font": "sans",
                        "arrowheadStart": "none",
                        "arrowheadEnd": "arrow",
                        "labelColor": "black",
                        "labelPosition": 0.5,
                        "richText": {"type": "doc", "content": []},
                        "scale": 1,
                    },
                },
            }
        ]

        normalized = normalize_generated_operations(storage, operations)
        arrow = normalized[0]["shape"]

        self.assertEqual(arrow["x"], 200)
        self.assertEqual(arrow["y"], 200)
        self.assertEqual(arrow["props"]["start"], {"x": 0, "y": 0})
        self.assertEqual(arrow["props"]["end"], {"x": 400, "y": 0})

    def test_infers_arrow_connection_from_absolute_points(self) -> None:
        storage = {
            "shapes": {
                "shape:left": _note("shape:left", 100, 100),
                "shape:right": _note("shape:right", 500, 100),
            }
        }
        operations = [
            {
                "op": "add_shape",
                "shape": {
                    "id": "shape:arrow",
                    "type": "arrow",
                    "x": 300,
                    "y": 350,
                    "rotation": 0,
                    "index": "a3",
                    "parentId": "page:page",
                    "isLocked": False,
                    "opacity": 1,
                    "meta": {},
                    "props": {
                        "kind": "arc",
                        "start": {"x": 200, "y": 200},
                        "end": {"x": 600, "y": 200},
                        "bend": 0,
                        "color": "black",
                        "fill": "none",
                        "dash": "solid",
                        "size": "m",
                        "font": "sans",
                        "arrowheadStart": "none",
                        "arrowheadEnd": "arrow",
                        "labelColor": "black",
                        "labelPosition": 0.5,
                        "richText": {"type": "doc", "content": []},
                        "scale": 1,
                    },
                },
            }
        ]

        normalized = normalize_generated_operations(storage, operations)
        arrow = normalized[0]["shape"]

        self.assertEqual(arrow["meta"][AGENT_CONNECTION_META_KEY]["startShapeId"], "shape:left")
        self.assertEqual(arrow["meta"][AGENT_CONNECTION_META_KEY]["endShapeId"], "shape:right")
        self.assertEqual(arrow["x"], 200)
        self.assertEqual(arrow["props"]["end"], {"x": 400, "y": 0})

    def test_rewrites_reverse_duplicate_arrow_add_into_label_update(self) -> None:
        base_storage = {
            "shapes": {
                "shape:left": _note("shape:left", 100, 100),
                "shape:right": _note("shape:right", 500, 100),
            }
        }
        existing_arrow = normalize_generated_operations(
            base_storage,
            [{"op": "add_shape", "shape": _arrow("shape:arrow", "shape:left", "shape:right", "old label")}],
        )[0]["shape"]
        storage = {
            "shapes": {
                **base_storage["shapes"],
                "shape:arrow": existing_arrow,
            }
        }
        operations = [
            {
                "op": "add_shape",
                "shape": _arrow("shape:new_arrow", "shape:right", "shape:left", "new label"),
            }
        ]

        normalized = normalize_generated_operations(storage, operations)

        self.assertEqual(
            normalized,
            [
                {
                    "op": "update_shape",
                    "shapeId": "shape:arrow",
                    "updates": {
                        "props": {
                            "richText": {
                                "type": "doc",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": "new label"}],
                                    }
                                ],
                            }
                        }
                    },
                }
            ],
        )

    def test_sanitize_repairs_top_level_arrow_updates(self) -> None:
        storage = {
            "shapes": {
                "shape:arrow": {
                    "id": "shape:arrow",
                    "type": "arrow",
                    "x": 300,
                    "y": 200,
                    "rotation": 0,
                    "index": "a3",
                    "parentId": "page:page",
                    "isLocked": False,
                    "opacity": 1,
                    "meta": {},
                    "props": {
                        "kind": "arc",
                        "start": {"x": 0, "y": 0},
                        "end": {"x": 100, "y": 0},
                        "bend": 0,
                        "color": "black",
                        "fill": "none",
                        "dash": "solid",
                        "size": "m",
                        "font": "sans",
                        "arrowheadStart": "none",
                        "arrowheadEnd": "arrow",
                        "labelColor": "black",
                        "labelPosition": 0.5,
                        "richText": {"type": "doc", "content": []},
                        "scale": 1,
                    },
                }
            }
        }
        operations = [
            {
                "op": "update_shape",
                "shapeId": "shape:arrow",
                "updates": {
                    "start": {"x": 200, "y": 200},
                    "end": {"x": 600, "y": 200},
                },
            }
        ]

        normalized = sanitize_operations_for_apply(storage, operations)
        updates = normalized[0]["updates"]

        self.assertNotIn("start", updates)
        self.assertNotIn("end", updates)
        self.assertIn("props", updates)
        self.assertEqual(updates["props"]["start"], {"x": 0.0, "y": 0.0})
        self.assertEqual(updates["props"]["end"], {"x": 400.0, "y": 0.0})
        self.assertEqual(updates["x"], 200.0)
        self.assertNotIn("y", updates)

    def test_sanitize_rewrites_reverse_duplicate_arrow_add_into_label_update(self) -> None:
        base_storage = {
            "shapes": {
                "shape:left": _note("shape:left", 100, 100),
                "shape:right": _note("shape:right", 500, 100),
            }
        }
        existing_arrow = normalize_generated_operations(
            base_storage,
            [{"op": "add_shape", "shape": _arrow("shape:arrow", "shape:left", "shape:right", "old label")}],
        )[0]["shape"]
        storage = {
            "shapes": {
                **base_storage["shapes"],
                "shape:arrow": existing_arrow,
            }
        }
        operations = [
            {
                "op": "add_shape",
                "shape": _arrow("shape:new_arrow", "shape:right", "shape:left", "new label"),
            }
        ]

        normalized = sanitize_operations_for_apply(storage, operations)

        self.assertEqual(
            normalized,
            [
                {
                    "op": "update_shape",
                    "shapeId": "shape:arrow",
                    "updates": {
                        "props": {
                            "richText": {
                                "type": "doc",
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [{"type": "text", "text": "new label"}],
                                    }
                                ],
                            }
                        }
                    },
                }
            ],
        )

    def test_compiles_semantic_update_for_existing_shape(self) -> None:
        storage = {"shapes": {"shape:note": _note("shape:note", 100, 100)}}
        compiled = compile_draft_operations(
            storage,
            [
                {
                    "op": "update_shape",
                    "shapeId": "shape:note",
                    "updates": {
                        "label": "Renamed note",
                        "color": "green",
                    },
                }
            ],
        )

        normalized = normalize_generated_operations(storage, compiled)
        updates = normalized[0]["updates"]["props"]

        self.assertNotIn("color", updates)
        self.assertEqual(
            updates["richText"],
            {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Renamed note"}]}]},
        )

    def test_compiles_semantic_geo_update_without_style_noise(self) -> None:
        storage = {"shapes": {"shape:geo": _geo("shape:geo", 100, 100)}}
        compiled = compile_draft_operations(
            storage,
            [
                {
                    "op": "update_shape",
                    "shapeId": "shape:geo",
                    "updates": {
                        "label": "Updated geo",
                        "color": "green",
                        "fill": "pattern",
                        "size": "xl",
                        "w": 280,
                    },
                }
            ],
        )

        normalized = normalize_generated_operations(storage, compiled)
        updates = normalized[0]["updates"]["props"]

        self.assertEqual(updates["w"], 280.0)
        self.assertNotIn("color", updates)
        self.assertNotIn("fill", updates)
        self.assertNotIn("size", updates)
        self.assertEqual(
            updates["richText"],
            {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Updated geo"}]}]},
        )


class ChangeCursorTests(unittest.TestCase):
    def test_get_change_cursor_uses_top_left_of_added_shape(self) -> None:
        cursor = get_change_cursor(
            {"shapes": {}},
            [{"op": "add_shape", "shape": _geo("shape:new", 320, 180)}],
        )

        self.assertEqual(cursor, {"x": 320, "y": 180})

    def test_get_change_cursor_uses_updated_shape_position(self) -> None:
        cursor = get_change_cursor(
            {"shapes": {"shape:1": _note("shape:1", 100, 120)}},
            [{"op": "update_shape", "shapeId": "shape:1", "updates": {"x": 280, "y": 360}}],
        )

        self.assertEqual(cursor, {"x": 280, "y": 360})

    def test_get_change_cursor_uses_deleted_shape_position(self) -> None:
        cursor = get_change_cursor(
            {"shapes": {"shape:1": _note("shape:1", 140, 260)}},
            [{"op": "delete_shape", "shapeId": "shape:1"}],
        )

        self.assertEqual(cursor, {"x": 140, "y": 260})


def _note(shape_id: str, x: float, y: float) -> dict:
    return {
        "id": shape_id,
        "type": "note",
        "x": x,
        "y": y,
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
            "richText": {"type": "doc", "content": []},
            "scale": 1,
        },
    }


def _geo(shape_id: str, x: float, y: float) -> dict:
    return {
        "id": shape_id,
        "type": "geo",
        "x": x,
        "y": y,
        "rotation": 0,
        "index": "a1",
        "parentId": "page:page",
        "isLocked": False,
        "opacity": 1,
        "meta": {},
        "props": {
            "geo": "rectangle",
            "w": DEFAULT_GEO_WIDTH,
            "h": DEFAULT_GEO_HEIGHT,
            "color": "black",
            "fill": "solid",
            "dash": "solid",
            "size": "m",
            "font": "sans",
            "align": "middle",
            "verticalAlign": "middle",
            "richText": {"type": "doc", "content": []},
            "labelColor": "black",
            "url": "",
            "growY": 0,
            "scale": 1,
        },
    }


def _arrow(shape_id: str, start_shape_id: str, end_shape_id: str, text: str = "") -> dict:
    paragraph = {"type": "paragraph"}
    if text:
        paragraph["content"] = [{"type": "text", "text": text}]

    return {
        "id": shape_id,
        "type": "arrow",
        "x": 0,
        "y": 0,
        "rotation": 0,
        "index": "a3",
        "parentId": "page:page",
        "isLocked": False,
        "opacity": 1,
        "meta": {
            AGENT_CONNECTION_META_KEY: {
                "startShapeId": start_shape_id,
                "endShapeId": end_shape_id,
                "startAnchor": {"x": 0.5, "y": 0.5},
                "endAnchor": {"x": 0.5, "y": 0.5},
            }
        },
        "props": {
            "kind": "arc",
            "start": {"x": 0, "y": 0},
            "end": {"x": 0, "y": 0},
            "bend": 0,
            "color": "black",
            "fill": "none",
            "dash": "solid",
            "size": "m",
            "font": "sans",
            "arrowheadStart": "none",
            "arrowheadEnd": "arrow",
            "labelColor": "black",
            "labelPosition": 0.5,
            "richText": {"type": "doc", "content": [paragraph]},
            "scale": 1,
        },
    }


if __name__ == "__main__":
    unittest.main()
