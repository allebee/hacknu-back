"""
Media generation route handlers — Higgsfield text-to-image & image-to-video.
"""

from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from openai import AsyncOpenAI

from app.config import OPENAI_API_KEY, AGENT_PROVIDER, GEMINI_API_KEY
from app.database import get_db
from app.debug import debug_print
from app.liveblocks import liveblocks
from app.higgsfield import generate_image, generate_video, check_status
from app.schemas import (
    GenerateImageRequest,
    GenerateImageResponse,
    GenerateVideoRequest,
    GenerateVideoResponse,
    GenerateStatusResponse,
)
from typing import Annotated

logger = logging.getLogger(__name__)

DbDep = Annotated[AsyncSession, Depends(get_db)]

generate_router = APIRouter(tags=["media"])


# ── LLM prompt crafter ────────────────────────────────────────────────

IMAGE_PROMPT_SYSTEM = """You are a prompt engineer for an AI image generator (Higgsfield).

Given the content of selected canvas shapes, craft an optimal image generation prompt.

Rules:
1. First decide if the selected content is suitable for image generation.
   - SUITABLE: visual concepts, design ideas, mood descriptions, product concepts, logos, scenes, illustrations, diagrams to visualize
   - NOT SUITABLE: task lists, schedules, meeting notes, code snippets, budget numbers, pure technical specs

2. If NOT suitable, return:
   {"suitable": false, "reason": "brief explanation why"}

3. If suitable, craft a detailed, descriptive prompt that:
   - Captures the essence of the selected shapes' content
   - Adds visual details (style, lighting, composition, colors)
   - Is optimized for text-to-image generation
   - Is 1-3 sentences maximum
   Return:
   {"suitable": true, "prompt": "your crafted image generation prompt", "aspect_ratio": "16:9 or 1:1 or 9:16"}

Return ONLY valid JSON, no other text."""


async def _craft_image_prompt(
    shapes_content: list[dict[str, str]],
    user_hint: str = "",
) -> dict:
    """Use LLM to craft an image generation prompt from shape content."""
    context_parts = []
    for shape in shapes_content:
        shape_type = shape.get("type", "unknown")
        text = shape.get("text", "")
        if text:
            context_parts.append(f"[{shape_type}] {text}")

    context = "\n".join(context_parts)
    if user_hint:
        context += f"\n\nUser hint: {user_hint}"

    user_message = f"Selected canvas shapes:\n{context}"

    if AGENT_PROVIDER == "gemini":
        client = AsyncOpenAI(
            api_key=GEMINI_API_KEY,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        model = "gemini-2.5-flash"
    else:
        client = AsyncOpenAI(api_key=OPENAI_API_KEY)
        model = "gpt-4o-mini"

    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": IMAGE_PROMPT_SYSTEM},
                {"role": "user", "content": user_message},
            ],
            temperature=0.4,
            max_tokens=500,
            response_format={"type": "json_object"},
        )
        import json
        raw = response.choices[0].message.content or "{}"
        return json.loads(raw)
    except Exception as e:
        logger.error(f"[GenerateImage] LLM prompt crafting failed: {e}")
        return {"suitable": False, "reason": f"Failed to analyze content: {e}"}


def _rich_text_to_plain_text(rich_text: object) -> str:
    """Extract plain text from Prosemirror rich text."""
    if not isinstance(rich_text, dict):
        return ""
    parts: list[str] = []
    for block in rich_text.get("content", []):
        if not isinstance(block, dict):
            continue
        for node in block.get("content", []):
            if isinstance(node, dict):
                text = node.get("text")
                if isinstance(text, str):
                    parts.append(text)
    return "".join(parts)


def _extract_shape_text(shape: dict) -> str:
    """Extract plain text from a tldraw shape."""
    props = shape.get("props", {})

    # Try richText first
    rich_text = props.get("richText")
    if isinstance(rich_text, dict):
        text = _rich_text_to_plain_text(rich_text)
        if text:
            return text

    # Try name (for frames)
    name = props.get("name")
    if isinstance(name, str) and name:
        return name

    # Try text field
    text = props.get("text")
    if isinstance(text, str) and text:
        return text

    return ""


# ── POST /generate/image ──────────────────────────────────────────────

@generate_router.post("/media/generate", response_model=GenerateImageResponse)
async def generate_image_endpoint(req: GenerateImageRequest, db: DbDep):
    """
    Generate an image from selected canvas shapes using Higgsfield.

    Flow:
    1. Read storage, extract text from selected shapes
    2. LLM decides if suitable → crafts optimized prompt
    3. Call Higgsfield text-to-image API
    4. Place resulting image on canvas as tldraw image shape
    """
    debug_print("routes.generate_image.request", req.model_dump())

    if not req.shape_ids:
        raise HTTPException(status_code=400, detail="No shapes selected")

    # 1. Get storage
    try:
        storage = await liveblocks.get_storage(req.room_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to read storage: {e}")

    # 2. Extract content from selected shapes
    shapes = storage.get("shapes", {})
    shapes_content: list[dict[str, str]] = []
    selected_positions: list[tuple[float, float]] = []

    for shape_id in req.shape_ids:
        shape = shapes.get(shape_id)
        if not shape or not isinstance(shape, dict):
            continue
        text = _extract_shape_text(shape)
        shape_type = shape.get("type", "unknown")
        shapes_content.append({"type": shape_type, "text": text, "id": shape_id})
        selected_positions.append((
            float(shape.get("x", 0)),
            float(shape.get("y", 0)),
        ))

    if not shapes_content:
        return GenerateImageResponse(
            status="skipped",
            reason="Selected shapes not found in canvas storage",
        )

    # Check if any shape has meaningful text
    all_text = " ".join(s["text"] for s in shapes_content).strip()
    if not all_text:
        return GenerateImageResponse(
            status="skipped",
            reason="Selected shapes contain no text content to generate an image from",
        )

    debug_print("routes.generate_image.shapes_content", shapes_content)

    # 3. LLM prompt crafting + suitability check
    llm_result = await _craft_image_prompt(shapes_content, req.user_hint)
    debug_print("routes.generate_image.llm_result", llm_result)

    if not llm_result.get("suitable", False):
        return GenerateImageResponse(
            status="skipped",
            reason=llm_result.get("reason", "Content not suitable for image generation"),
        )

    crafted_prompt = llm_result.get("prompt", "")
    aspect_ratio = llm_result.get("aspect_ratio", "16:9")

    if not crafted_prompt:
        return GenerateImageResponse(
            status="skipped",
            reason="Failed to generate a meaningful image prompt",
        )

    # 4. Calculate placement position (to the right of selected shapes)
    if selected_positions:
        max_x = max(p[0] for p in selected_positions)
        avg_y = sum(p[1] for p in selected_positions) / len(selected_positions)
        place_x = max_x + 300  # offset to the right
        place_y = avg_y
    else:
        place_x, place_y = 400, 300

    # 5. Call Higgsfield
    try:
        result = await generate_image(
            crafted_prompt,
            aspect_ratio=aspect_ratio,
        )
        debug_print("routes.generate_image.higgsfield_result", result)
    except Exception as e:
        logger.error(f"[GenerateImage] Higgsfield API failed: {e}")
        return GenerateImageResponse(
            status="failed",
            reason=f"Image generation failed: {e}",
            prompt_used=crafted_prompt,
        )

    if result.get("status") != "completed" or not result.get("image_url"):
        return GenerateImageResponse(
            status="failed",
            reason=f"Generation status: {result.get('status', 'unknown')}",
            request_id=result.get("request_id"),
            prompt_used=crafted_prompt,
        )

    image_url = result["image_url"]

    # 6. Determine image dimensions based on aspect ratio
    if aspect_ratio == "1:1":
        img_w, img_h = 400, 400
    elif aspect_ratio == "9:16":
        img_w, img_h = 350, 620
    else:  # 16:9
        img_w, img_h = 500, 280

    # 7. Create tldraw asset + image shape and patch into Liveblocks
    asset_id = f"asset:{uuid.uuid4().hex[:16]}"
    shape_id = f"shape:{uuid.uuid4().hex[:16]}"

    # tldraw requires an asset entry for the image
    asset_entry = {
        "id": asset_id,
        "type": "image",
        "typeName": "asset",
        "props": {
            "name": crafted_prompt[:50],
            "src": image_url,
            "w": img_w,
            "h": img_h,
            "mimeType": "image/png",
            "isAnimated": False,
        },
        "meta": {
            "source": "higgsfield",
            "prompt": crafted_prompt,
            "request_id": result.get("request_id", ""),
        },
    }

    image_shape = {
        "id": shape_id,
        "type": "image",
        "x": place_x,
        "y": place_y,
        "rotation": 0,
        "index": "a1",
        "parentId": "page:page",
        "isLocked": False,
        "opacity": 1,
        "meta": {
            "source": "higgsfield",
            "prompt": crafted_prompt,
        },
        "props": {
            "w": img_w,
            "h": img_h,
            "assetId": asset_id,
            "crop": None,
        },
    }

    try:
        patch_ops = [
            {"op": "add", "path": f"/assets/{asset_id}", "value": asset_entry},
            {"op": "add", "path": f"/shapes/{shape_id}", "value": image_shape},
        ]
        debug_print("routes.generate_image.patch_ops", patch_ops)
        await liveblocks.patch_storage(req.room_id, patch_ops)
    except Exception as e:
        logger.error(f"[GenerateImage] Failed to patch image to storage: {e}")
        # Still return the URL so frontend can use it
        return GenerateImageResponse(
            status="completed",
            request_id=result.get("request_id"),
            prompt_used=crafted_prompt,
            image_url=image_url,
            shape_id=None,
        )

    logger.info(f"[GenerateImage] Image placed on canvas: shape_id={shape_id}")

    return GenerateImageResponse(
        status="completed",
        request_id=result.get("request_id"),
        prompt_used=crafted_prompt,
        image_url=image_url,
        shape_id=shape_id,
    )


# ── POST /generate/video ──────────────────────────────────────────────

@generate_router.post("/media/generate/video", response_model=GenerateVideoResponse)
async def generate_video_endpoint(req: GenerateVideoRequest, db: DbDep):
    """Generate a video from an image using Higgsfield image-to-video."""
    debug_print("routes.generate_video.request", req.model_dump())

    try:
        result = await generate_video(
            req.image_url,
            req.prompt,
            duration=req.duration,
        )
        debug_print("routes.generate_video.result", result)
    except Exception as e:
        logger.error(f"[GenerateVideo] Higgsfield API failed: {e}")
        raise HTTPException(status_code=502, detail=f"Video generation failed: {e}")

    return GenerateVideoResponse(
        status=result.get("status", "failed"),
        request_id=result.get("request_id"),
        video_url=result.get("video_url"),
    )


# ── GET /generate/status/{request_id} ─────────────────────────────────

@generate_router.get("/media/status/{request_id}", response_model=GenerateStatusResponse)
async def get_generation_status(request_id: str):
    """Poll the status of a Higgsfield generation request."""
    try:
        result = await check_status(request_id)
    except Exception as e:
        logger.error(f"[GenerateStatus] Failed to check status: {e}")
        raise HTTPException(status_code=502, detail=f"Status check failed: {e}")

    return GenerateStatusResponse(
        status=result.get("status", "unknown"),
        request_id=request_id,
        image_url=result.get("image_url"),
        video_url=result.get("video_url"),
    )
