"""
Action validator — sanitizes and validates actions before returning to the client.

Checks:
- Known action_type
- Coordinate bounds (keep shapes on reasonable canvas area)
- Text length limits
- Shape ID format
- Required fields presence
"""

from schemas import ACTION_TYPES

# Canvas bounds — reasonable limits
MAX_COORD = 10000
MIN_COORD = -10000
MAX_DIMENSION = 5000
MIN_DIMENSION = 10
MAX_TEXT_LENGTH = 200
MAX_ACTIONS = 30


def validate_actions(actions: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Validate and sanitize a list of raw action dicts.
    
    Returns:
        (valid_actions, errors) — valid actions list and list of error strings
    """
    valid = []
    errors = []

    if len(actions) > MAX_ACTIONS:
        errors.append(f"Too many actions ({len(actions)}), max is {MAX_ACTIONS}. Truncating.")
        actions = actions[:MAX_ACTIONS]

    for i, action in enumerate(actions):
        action_errors = _validate_single(action, i)
        if action_errors:
            errors.extend(action_errors)
        else:
            sanitized = _sanitize(action)
            valid.append(sanitized)

    return valid, errors


def _validate_single(action: dict, index: int) -> list[str]:
    """Validate a single action dict. Returns list of errors (empty if valid)."""
    errors = []
    prefix = f"Action[{index}]"

    # Check action_type exists
    action_type = action.get("action_type") or action.get("_type")
    if not action_type:
        errors.append(f"{prefix}: missing action_type")
        return errors

    if action_type not in ACTION_TYPES:
        errors.append(f"{prefix}: unknown action_type '{action_type}'")
        return errors

    # Coordinate checks for create actions
    if action_type in ("create_note", "create_frame"):
        x = action.get("x", 0)
        y = action.get("y", 0)
        if not (MIN_COORD <= x <= MAX_COORD):
            errors.append(f"{prefix}: x={x} out of bounds [{MIN_COORD}, {MAX_COORD}]")
        if not (MIN_COORD <= y <= MAX_COORD):
            errors.append(f"{prefix}: y={y} out of bounds [{MIN_COORD}, {MAX_COORD}]")

        # Dimensions are clamped during sanitization, not rejected

    # Text length is truncated during sanitization, not rejected

    # Shape ID references
    if action_type == "move_shapes":
        ids = action.get("shape_ids", [])
        if not ids:
            errors.append(f"{prefix}: move_shapes requires non-empty shape_ids")

    if action_type == "delete_shapes":
        ids = action.get("shape_ids", [])
        if not ids:
            errors.append(f"{prefix}: delete_shapes requires non-empty shape_ids")

    return errors


def _sanitize(action: dict) -> dict:
    """Sanitize an action — clamp values, truncate text, etc."""
    sanitized = dict(action)
    action_type = sanitized.get("action_type") or sanitized.get("_type")

    # Ensure action_type is set
    if "action_type" not in sanitized:
        sanitized["action_type"] = action_type

    # Clamp coordinates
    if action_type in ("create_note", "create_frame"):
        sanitized["x"] = max(MIN_COORD, min(MAX_COORD, sanitized.get("x", 0)))
        sanitized["y"] = max(MIN_COORD, min(MAX_COORD, sanitized.get("y", 0)))
        sanitized["w"] = max(MIN_DIMENSION, min(MAX_DIMENSION, sanitized.get("w", 200)))
        sanitized["h"] = max(MIN_DIMENSION, min(MAX_DIMENSION, sanitized.get("h", 100)))

    # Truncate text
    if action_type in ("create_note", "update_text"):
        text = sanitized.get("text", "")
        if len(text) > MAX_TEXT_LENGTH:
            sanitized["text"] = text[:MAX_TEXT_LENGTH - 3] + "..."

    return sanitized
