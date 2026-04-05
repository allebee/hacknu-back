"""
Higgsfield API client for text-to-image and image-to-video generation.

API docs: https://docs.higgsfield.ai
Base URL: https://platform.higgsfield.ai
Auth: Authorization: Key {key_id}:{key_secret}
Flow: submit → poll status_url → completed (images[].url / video.url)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Literal

import httpx

from app.config import HIGGSFIELD_API_KEY_ID, HIGGSFIELD_API_KEY_SECRET

logger = logging.getLogger(__name__)

BASE_URL = "https://platform.higgsfield.ai"

# Models
TEXT_TO_IMAGE_MODEL = "higgsfield-ai/soul/standard"
IMAGE_TO_VIDEO_MODEL = "higgsfield-ai/dop/preview"

# Polling config
POLL_INTERVAL_SECONDS = 3
MAX_POLL_ATTEMPTS = 60  # ~3 minutes max wait


def _auth_header() -> dict[str, str]:
    return {
        "Authorization": f"Key {HIGGSFIELD_API_KEY_ID}:{HIGGSFIELD_API_KEY_SECRET}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


async def _poll_until_done(
    status_url: str,
    *,
    timeout_attempts: int = MAX_POLL_ATTEMPTS,
) -> dict:
    """Poll a Higgsfield status_url until terminal state."""
    headers = _auth_header()
    async with httpx.AsyncClient(timeout=30) as client:
        for attempt in range(timeout_attempts):
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            r = await client.get(status_url, headers=headers)
            r.raise_for_status()
            data = r.json()
            status = data.get("status")
            logger.info(
                f"[Higgsfield] poll attempt={attempt+1} status={status}"
            )
            if status in ("completed", "failed", "nsfw"):
                return data
    raise TimeoutError(
        f"Higgsfield generation did not complete after {timeout_attempts} polls"
    )


async def generate_image(
    prompt: str,
    *,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
) -> dict:
    """
    Generate an image from text using Higgsfield.

    Returns dict with keys:
        - status: "completed" | "failed" | "nsfw"
        - image_url: str (only if completed)
        - request_id: str
    """
    headers = _auth_header()
    payload = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }

    logger.info(f"[Higgsfield] text-to-image: {prompt[:80]}...")

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/{TEXT_TO_IMAGE_MODEL}",
            json=payload,
            headers=headers,
        )
        r.raise_for_status()
        submit_data = r.json()

    request_id = submit_data.get("request_id", "")
    status_url = submit_data.get("status_url", "")
    logger.info(f"[Higgsfield] queued request_id={request_id}")

    if not status_url:
        return {"status": "failed", "request_id": request_id, "error": "No status_url in response"}

    result = await _poll_until_done(status_url)

    response: dict = {
        "status": result.get("status", "failed"),
        "request_id": request_id,
    }

    if result.get("status") == "completed":
        images = result.get("images", [])
        if images:
            response["image_url"] = images[0].get("url", "")

    return response


async def submit_image_generation(
    prompt: str,
    *,
    aspect_ratio: str = "16:9",
    resolution: str = "720p",
) -> dict:
    """
    Submit an image generation request without waiting for completion.

    Returns dict with keys:
        - request_id: str
        - status_url: str
        - status: str (usually "queued")
    """
    headers = _auth_header()
    payload = {
        "prompt": prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": resolution,
    }

    logger.info(f"[Higgsfield] submit text-to-image: {prompt[:80]}...")

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/{TEXT_TO_IMAGE_MODEL}",
            json=payload,
            headers=headers,
        )
        r.raise_for_status()
        return r.json()


async def check_status(request_id: str) -> dict:
    """Check the status of a Higgsfield generation request."""
    headers = _auth_header()
    status_url = f"{BASE_URL}/requests/{request_id}/status"

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(status_url, headers=headers)
        r.raise_for_status()
        data = r.json()

    response: dict = {
        "status": data.get("status", "unknown"),
        "request_id": request_id,
    }

    if data.get("status") == "completed":
        images = data.get("images", [])
        if images:
            response["image_url"] = images[0].get("url", "")
        video = data.get("video")
        if isinstance(video, dict) and video.get("url"):
            response["video_url"] = video["url"]

    return response


async def generate_video(
    image_url: str,
    prompt: str = "",
    *,
    duration: int = 5,
) -> dict:
    """
    Generate a video from an image using Higgsfield.

    Returns dict with keys:
        - status: "completed" | "failed" | "nsfw"
        - video_url: str (only if completed)
        - request_id: str
    """
    headers = _auth_header()
    payload: dict = {
        "image_url": image_url,
        "duration": duration,
    }
    if prompt:
        payload["prompt"] = prompt

    logger.info(f"[Higgsfield] image-to-video: image={image_url[:60]}...")

    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BASE_URL}/{IMAGE_TO_VIDEO_MODEL}",
            json=payload,
            headers=headers,
        )
        r.raise_for_status()
        submit_data = r.json()

    request_id = submit_data.get("request_id", "")
    status_url = submit_data.get("status_url", "")
    logger.info(f"[Higgsfield] queued video request_id={request_id}")

    if not status_url:
        return {"status": "failed", "request_id": request_id, "error": "No status_url"}

    result = await _poll_until_done(status_url)

    response: dict = {
        "status": result.get("status", "failed"),
        "request_id": request_id,
    }

    if result.get("status") == "completed":
        video = result.get("video")
        if isinstance(video, dict) and video.get("url"):
            response["video_url"] = video["url"]

    return response
