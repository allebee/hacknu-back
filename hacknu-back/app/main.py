"""
FastAPI entry point — replaces the old main.py.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import AGENT_PROVIDER, AGENT_MODEL
from app.routes import complete_router, agents_router, agent_router, transcript_router
from app.generate_routes import generate_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("🚀 Canvas Agent service starting...")
    logger.info(f"   Provider: {AGENT_PROVIDER}")
    logger.info(f"   Model: {AGENT_MODEL}")
    yield
    logger.info("Canvas Agent service shutting down.")


app = FastAPI(
    title="Canvas Agent Service",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount routers
app.include_router(complete_router)
app.include_router(agents_router)
app.include_router(agent_router)
app.include_router(transcript_router)
app.include_router(generate_router)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "provider": AGENT_PROVIDER,
        "model": AGENT_MODEL,
    }
