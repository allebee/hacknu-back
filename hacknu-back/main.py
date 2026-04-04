"""
FastAPI entry point for the AI Brainstorm Canvas agent service.

Endpoints:
  POST /api/agent/plan  — main planning endpoint
  GET  /api/health       — health check
"""

import os
import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from schemas import PlanRequest, PlanResponse
from planner import plan

# Load .env from parent directory (hacknu/.env)
load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Agent service starting...")
    logger.info(f"   Provider: {os.getenv('AGENT_PROVIDER', 'openai')}")
    logger.info(f"   Model: {os.getenv('AGENT_MODEL', 'gpt-4o')}")
    yield
    logger.info("Agent service shutting down.")


app = FastAPI(
    title="AI Brainstorm Canvas — Agent Service",
    version="0.1.0",
    lifespan=lifespan,
)

# CORS — allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",     # Vite dev
        "http://localhost:3000",     # Next.js dev (if frontend migrates)
        "http://127.0.0.1:5173",
        "http://127.0.0.1:3000",
        "*",                         # Hackathon — allow all for demo
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "provider": os.getenv("AGENT_PROVIDER", "openai"),
        "model": os.getenv("AGENT_MODEL", "gpt-4o"),
    }


@app.post("/api/agent/plan", response_model=PlanResponse)
async def agent_plan(req: PlanRequest):
    """
    Main agent planning endpoint.
    
    Accepts board context + mode, returns structured canvas actions.
    """
    logger.info(f"📋 Plan request: mode={req.mode}, intent=\"{req.user_intent}\", "
                f"selection={len(req.selection)}, visible={len(req.visible_shapes)}")

    try:
        result = await plan(req)
        logger.info(f"✅ Returned {len(result.actions)} actions, reasoning: {result.reasoning[:100]}")
        return result
    except Exception as e:
        logger.error(f"❌ Plan failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


# ── Run with: uvicorn main:app --reload --port 8000 ──────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
