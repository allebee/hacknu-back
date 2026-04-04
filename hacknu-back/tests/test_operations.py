from __future__ import annotations

import unittest

from app.operations import AGENT_CONNECTION_META_KEY, normalize_generated_operations, sanitize_operations_for_apply


class OperationNormalizationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
