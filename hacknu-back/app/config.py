"""
Application configuration — reads from environment variables.
"""

import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))


DATABASE_URL: str = os.getenv(
    "DATABASE_URL",
    "postgresql+asyncpg://hacknu:hacknu@db:5432/hacknu",
)

# Sync URL for Alembic (replaces asyncpg with psycopg2)
DATABASE_URL_SYNC: str = DATABASE_URL.replace("+asyncpg", "")

LIVEBLOCKS_SECRET_KEY: str = os.getenv("LIVEBLOCKS_SECRET_KEY", "")

OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
GEMINI_API_KEY: str = os.getenv("GEMINI_API", "")
AGENT_PROVIDER: str = os.getenv("AGENT_PROVIDER", "openai").lower()
AGENT_MODEL: str = os.getenv("AGENT_MODEL", "gpt-4o")
